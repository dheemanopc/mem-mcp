"""Bearer-token authentication middleware for /mcp routes.

Per LLD §4.3.2 and spec §5.2:
  - Extract Bearer JWT from Authorization header
  - Validate via JwtValidator (signature, iss, exp, ...)
  - Resolve tenant via tenant_identities JOIN tenants LEFT JOIN oauth_clients
  - Reject with 401 + WWW-Authenticate (RFC 6750) on auth failures
  - Reject with 403 on suspended / pending_deletion tenants
  - Stash TenantContext on request.state.tenant_ctx
  - Best-effort fire-and-forget UPDATE of last_seen_at + last_used_at

Skips any path not under ``mcp_path_prefix`` (default '/mcp'). Web routes
(/api/web/*, /auth/*) use cookie sessions (T-8.x), not Bearer.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import UUID

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mem_mcp.auth.jwt_validator import JwtError, JwtValidator
from mem_mcp.db import system_tx
from mem_mcp.logging_setup import get_logger

if TYPE_CHECKING:
    import asyncpg


_log = get_logger("mem_mcp.auth.middleware")

TenantStatus = Literal["active", "suspended", "pending_deletion", "deleted"]
ResolutionOutcome = Literal["not_found", "active", "suspended", "pending_deletion", "deleted"]


@dataclass(frozen=True)
class TenantContext:
    """Per-request authentication result; available as request.state.tenant_ctx."""

    tenant_id: UUID
    identity_id: UUID
    client_id: str
    scopes: frozenset[str]


@dataclass(frozen=True)
class TenantResolution:
    """Result of cognito_sub + client_id → DB lookup."""

    tenant_id: UUID | None        # None when not_found
    identity_id: UUID | None
    tenant_status: ResolutionOutcome
    client_known: bool
    client_disabled: bool


# --------------------------------------------------------------------------
# Protocols (test seams)
# --------------------------------------------------------------------------


class TenantResolver(Protocol):
    async def resolve(self, cognito_sub: str, client_id: str) -> TenantResolution: ...


class TouchSink(Protocol):
    async def touch(self, identity_id: UUID, client_id: str) -> None: ...


# --------------------------------------------------------------------------
# Production implementations
# --------------------------------------------------------------------------


class DbTenantResolver:
    """Production resolver: single LEFT JOIN against the asyncpg pool."""

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    async def resolve(self, cognito_sub: str, client_id: str) -> TenantResolution:
        async with system_tx(self._pool) as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  ti.tenant_id        AS tenant_id,
                  ti.id               AS identity_id,
                  t.status            AS tenant_status,
                  (oc.id IS NOT NULL) AS client_known,
                  COALESCE(oc.disabled, false) AS client_disabled
                FROM tenant_identities ti
                JOIN tenants t   ON t.id = ti.tenant_id
                LEFT JOIN oauth_clients oc ON oc.id = $2
                WHERE ti.cognito_sub = $1
                """,
                cognito_sub,
                client_id,
            )
        if row is None:
            return TenantResolution(None, None, "not_found", False, False)
        return TenantResolution(
            tenant_id=row["tenant_id"],
            identity_id=row["identity_id"],
            tenant_status=row["tenant_status"],
            client_known=bool(row["client_known"]),
            client_disabled=bool(row["client_disabled"]),
        )


class DbTouch:
    """Best-effort UPDATE of last_seen_at + last_used_at; never raises to caller."""

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    async def touch(self, identity_id: UUID, client_id: str) -> None:
        try:
            async with system_tx(self._pool) as conn:
                await conn.execute(
                    "UPDATE tenant_identities SET last_seen_at = now() WHERE id = $1",
                    identity_id,
                )
                await conn.execute(
                    "UPDATE oauth_clients SET last_used_at = now() WHERE id = $1",
                    client_id,
                )
        except Exception as exc:  # noqa: BLE001 — fire-and-forget, never propagate
            _log.warning(
                "touch_last_seen_failed",
                identity_id=str(identity_id),
                client_id=client_id,
                error=str(exc)[:200],
            )


# --------------------------------------------------------------------------
# Middleware factory
# --------------------------------------------------------------------------


def make_bearer_middleware(
    validator: JwtValidator,
    resolver: TenantResolver,
    touch: TouchSink,
    resource_metadata_url: str,
    mcp_path_prefix: str = "/mcp",
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """Return an ASGI HTTP middleware enforcing Bearer auth on mcp_path_prefix."""

    async def bearer_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Skip non-/mcp paths entirely
        if not request.url.path.startswith(mcp_path_prefix):
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return _unauthorized(resource_metadata_url, "missing_token", "Bearer token required")
        token = auth[len("Bearer "):].strip()
        if not token:
            return _unauthorized(resource_metadata_url, "missing_token", "empty token")

        try:
            claims = await validator.validate(token)
        except JwtError as exc:
            return _unauthorized(resource_metadata_url, "invalid_token", exc.code)

        resolution = await resolver.resolve(claims.sub, claims.client_id)

        # Reject reasons (check tenant first, before client)
        if resolution.tenant_status == "not_found":
            return _unauthorized(resource_metadata_url, "invalid_token", "no_tenant_for_sub")
        if not resolution.client_known or resolution.client_disabled:
            return _unauthorized(
                resource_metadata_url,
                "invalid_token",
                "client_revoked_or_unknown",
            )
        if resolution.tenant_status == "deleted":
            return _unauthorized(resource_metadata_url, "invalid_token", "tenant_deleted")
        if resolution.tenant_status == "suspended":
            return _forbidden("account_suspended")
        if resolution.tenant_status == "pending_deletion":
            return _forbidden("account_deletion_pending")

        # active — succeed
        assert resolution.tenant_id is not None and resolution.identity_id is not None
        request.state.tenant_ctx = TenantContext(
            tenant_id=resolution.tenant_id,
            identity_id=resolution.identity_id,
            client_id=claims.client_id,
            scopes=frozenset(claims.scopes),
        )

        # Fire-and-forget last_seen / last_used update
        # (asyncio.create_task is safe inside an async middleware running on the loop)
        asyncio.create_task(touch.touch(resolution.identity_id, claims.client_id))

        return await call_next(request)

    return bearer_middleware


# --------------------------------------------------------------------------
# Response helpers
# --------------------------------------------------------------------------


def _www_authenticate(resource_metadata_url: str, error: str, error_description: str) -> str:
    """Build RFC 6750 §3 WWW-Authenticate header value."""
    return (
        f'Bearer realm="mem-mcp", '
        f'resource_metadata="{resource_metadata_url}", '
        f'error="{error}", '
        f'error_description="{error_description}"'
    )


def _unauthorized(resource_metadata_url: str, error: str, reason: str) -> Response:
    return JSONResponse(
        status_code=401,
        content={"error": "invalid_token" if error == "invalid_token" else error, "reason": reason},
        headers={"WWW-Authenticate": _www_authenticate(resource_metadata_url, error, reason)},
    )


def _forbidden(error_code: str) -> Response:
    return JSONResponse(status_code=403, content={"error": error_code})
