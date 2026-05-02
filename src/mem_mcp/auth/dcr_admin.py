"""DCR admin endpoints (RFC 7592) — GET/DELETE /oauth/register/{client_id}.

Authenticated by the one-time registration_access_token issued at registration
time (sha256 stored in oauth_clients.registration_access_token_hash). NOT
authenticated by Cognito JWT — this is a separate auth scheme for client-self-management.

Per LLD §4.3 + spec §6.5.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import APIRouter, HTTPException, Request, status
from starlette.responses import JSONResponse, Response

from mem_mcp.db import system_tx
from mem_mcp.logging_setup import get_logger

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


_log = get_logger("mem_mcp.auth.dcr_admin")


# --------------------------------------------------------------------------
# Protocols (test seams)
# --------------------------------------------------------------------------


class OauthClientLookup(Protocol):
    async def fetch(self, client_id: str) -> dict[str, Any] | None:
        """Return the row dict for client_id, or None if not found.

        Expected keys: id, software_id, client_name, redirect_uris, scope,
        registration_access_token_hash, disabled, deleted_at.
        """
        ...


class OauthClientDeleter(Protocol):
    async def delete(self, client_id: str) -> bool:
        """Mark/remove the client locally. Return True if a row was affected."""
        ...


class CognitoClientDeleter(Protocol):
    async def delete_user_pool_client(self, client_id: str) -> None: ...


# --------------------------------------------------------------------------
# Production implementations
# --------------------------------------------------------------------------


class DbOauthClientLookup:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def fetch(self, client_id: str) -> dict[str, Any] | None:
        async with system_tx(self._pool) as conn:
            row = await conn.fetchrow(
                """
                SELECT id, software_id, client_name, redirect_uris, scope,
                       registration_access_token_hash, disabled, deleted_at
                FROM oauth_clients
                WHERE id = $1 AND deleted_at IS NULL
                """,
                client_id,
            )
        return dict(row) if row is not None else None


class DbOauthClientDeleter:
    """Soft-deletes the client (sets deleted_at = now()).

    A separate cleanup job (T-4.9) hard-deletes from Cognito + the row at
    the daily run; the soft-delete here ensures /mcp requests fail
    immediately (Bearer middleware checks disabled OR via JOIN; row gone =
    client_known=false).
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def delete(self, client_id: str) -> bool:
        async with system_tx(self._pool) as conn:
            result = await conn.execute(
                """
                UPDATE oauth_clients
                SET deleted_at = now(), disabled = true
                WHERE id = $1 AND deleted_at IS NULL
                """,
                client_id,
            )
        # asyncpg returns "UPDATE N" — parse the affected count
        return result.startswith("UPDATE ") and int(result.split()[-1]) > 0


class BotoCognitoClientDeleter:
    """Production CognitoClientDeleter using boto3 cognito-idp."""

    def __init__(self, user_pool_id: str, region: str) -> None:
        self.user_pool_id = user_pool_id
        self.region = region

    async def delete_user_pool_client(self, client_id: str) -> None:
        import asyncio

        import boto3  # type: ignore[import-untyped]

        def _call() -> None:
            client = boto3.client("cognito-idp", region_name=self.region)
            client.delete_user_pool_client(
                UserPoolId=self.user_pool_id,
                ClientId=client_id,
            )

        await asyncio.to_thread(_call)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _www_authenticate_header(realm: str = "dcr-admin", error: str = "invalid_token") -> str:
    return f'Bearer realm="{realm}", error="{error}"'


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[len("Bearer ") :].strip()
    return token or None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _verify_token(presented: str, stored_hash: str) -> bool:
    """Constant-time comparison via secrets.compare_digest."""
    return secrets.compare_digest(_hash_token(presented), stored_hash)


# --------------------------------------------------------------------------
# Router factory
# --------------------------------------------------------------------------


def make_dcr_admin_router(
    *,
    lookup: OauthClientLookup,
    db_deleter: OauthClientDeleter,
    cognito_deleter: CognitoClientDeleter,
    resource_url: str,
) -> APIRouter:
    """Build the /oauth/register/{client_id} GET+DELETE router."""
    router = APIRouter(tags=["dcr-admin"])

    async def _authenticated_row(request: Request, client_id: str) -> dict[str, Any]:
        """Look up the client + verify the bearer token. Raises HTTPException on failure."""
        token = _extract_bearer(request)
        if token is None:
            raise HTTPException(
                status_code=401,
                detail={"error": "invalid_token", "reason": "missing_bearer"},
                headers={"WWW-Authenticate": _www_authenticate_header()},
            )

        row = await lookup.fetch(client_id)
        if row is None or not _verify_token(token, row["registration_access_token_hash"]):
            # Same response whether unknown id OR wrong token — no enumeration
            raise HTTPException(
                status_code=401,
                detail={"error": "invalid_token", "reason": "unknown_or_wrong_token"},
                headers={"WWW-Authenticate": _www_authenticate_header()},
            )
        return row

    @router.get("/oauth/register/{client_id}")
    async def get_client(client_id: str, request: Request) -> JSONResponse:
        row = await _authenticated_row(request, client_id)
        # RFC 7592 representation — return the same RFC 7591 fields the client received at registration
        return JSONResponse(
            content={
                "client_id": row["id"],
                "client_name": row["client_name"],
                "redirect_uris": list(row["redirect_uris"]),
                "scope": row["scope"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "registration_client_uri": f"{resource_url}/oauth/register/{row['id']}",
                "software_id": row["software_id"],
                # Note: the registration_access_token itself is NOT echoed back —
                # the caller already has it; we only stored its hash.
            }
        )

    @router.delete("/oauth/register/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_client(client_id: str, request: Request) -> Response:
        # Auth check first
        await _authenticated_row(request, client_id)

        # Soft-delete locally
        local_deleted = await db_deleter.delete(client_id)
        if not local_deleted:
            # Race: someone else just deleted it. Treat as success.
            _log.info("dcr_admin_delete_already_gone", client_id=client_id)

        # Best-effort: delete from Cognito too. If Cognito call fails, the local
        # soft-delete is enough to revoke access (Bearer middleware checks
        # client_known + disabled). Cleanup job T-4.9 will retry.
        try:
            await cognito_deleter.delete_user_pool_client(client_id)
        except Exception as exc:
            _log.warning(
                "dcr_admin_cognito_delete_failed",
                client_id=client_id,
                error=str(exc)[:200],
            )

        # TODO(T-5.12): audit oauth.client_revoked
        return Response(status_code=204)

    return router
