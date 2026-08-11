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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import UUID

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mem_mcp.auth.jwt_validator import JwtError, JwtValidator
from mem_mcp.db import system_tx
from mem_mcp.identity.personal_team import ensure_personal_team
from mem_mcp.logging_setup import get_logger

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


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

    tenant_id: UUID | None  # None when not_found
    identity_id: UUID | None
    tenant_status: ResolutionOutcome
    client_known: bool
    client_disabled: bool


# --------------------------------------------------------------------------
# Protocols (test seams)
# --------------------------------------------------------------------------


class TenantResolver(Protocol):
    async def resolve(
        self,
        cognito_sub: str,
        client_id: str,
        email_hint: str | None = None,
    ) -> TenantResolution: ...


class TouchSink(Protocol):
    async def touch(self, identity_id: UUID, client_id: str) -> None: ...


# --------------------------------------------------------------------------
# Production implementations
# --------------------------------------------------------------------------


async def _create_personal_team_and_assign(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    email_hint: str,
    cognito_sub: str,
) -> None:
    """Localdev auto-create shim over the shared personal-team provisioner.

    Kept as a thin wrapper so this module's call sites and tests are unchanged.
    The implementation lives in ``identity.personal_team`` because the prod
    signup path (``jobs.reconcile_signups``) needs the identical shape — the two
    had drifted, and the reconcile path was missing it entirely, which left real
    signups unable to use MCP.
    """
    await ensure_personal_team(conn, tenant_id, email_hint, cognito_sub)


class DbTenantResolver:
    """Production resolver: single LEFT JOIN against the asyncpg pool.

    When ``auto_create_for_localdev`` is True AND a non-empty ``email_hint``
    is passed AND the lookup returns no row, this resolver INSERTs a
    tenant + tenant_identity from the claim (sub + email) and re-runs the
    lookup. Intended ONLY for localhost dev with the cognito-local sidecar
    — main.py gates this on MEM_MCP_COGNITO_ISSUER_URL being set, which
    is empty in prod. Removes the bootstrap two-step (sign in → restart →
    admin) for localdev users: first valid JWT auto-provisions the row.
    """

    def __init__(self, pool: asyncpg.Pool, auto_create_for_localdev: bool = False) -> None:
        self._pool = pool
        self._auto_create = auto_create_for_localdev

    async def resolve(
        self,
        cognito_sub: str,
        client_id: str,
        email_hint: str | None = None,
    ) -> TenantResolution:
        async with system_tx(self._pool) as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  ti.tenant_id        AS tenant_id,
                  ti.id               AS identity_id,
                  ti.default_team_id  AS default_team_id,
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
            # Localdev: also auto-create the oauth_clients row if missing.
            # Same gate (auto_create + localdev). The client_id from a
            # cognito-local JWT isn't pre-registered via DCR in the mem-mcp
            # DB, so the LEFT JOIN above sets client_known=false even when
            # the tenant exists. Surface this as the same auto-provision path.
            # Note: NO nested `conn.transaction()` here — system_tx is already
            # a transaction; nested savepoints had a visibility quirk where
            # the first call's re-fetch couldn't see the just-inserted client.
            need_oauth_client = (
                self._auto_create and client_id and (row is None or not row["client_known"])
            )
            need_full_tenant = row is None and self._auto_create and email_hint
            need_team_backfill = (
                row is not None
                and self._auto_create
                and email_hint
                and row["default_team_id"] is None
            )
            if need_oauth_client:
                await conn.execute(
                    "INSERT INTO oauth_clients "
                    "(id, redirect_uris, scope, registration_payload, review_status) "
                    "VALUES ($1, ARRAY[]::text[], 'memory.read memory.write', "
                    "'{}'::jsonb, 'auto_allowed') "
                    "ON CONFLICT (id) DO NOTHING",
                    client_id,
                )
                _log.info("oauth_client_auto_created_for_localdev", client_id=client_id)

            if need_full_tenant:
                assert email_hint is not None  # narrows for mypy
                # Localdev-only path: auto-provision tenant + identity + team
                # + default_team_id assignment from JWT claims so the first
                # valid token Just Works (no manual psql insert or service
                # restart). Mirrors migration 0026's personal-team backfill.
                _log.info(
                    "tenant_auto_created_for_localdev",
                    cognito_sub=cognito_sub,
                    email_hint=email_hint,
                )
                new_tenant_id = await conn.fetchval(
                    "INSERT INTO tenants (email, status) VALUES ($1, 'active') "
                    "ON CONFLICT (email) DO UPDATE SET status='active' RETURNING id",
                    email_hint,
                )
                await _create_personal_team_and_assign(conn, new_tenant_id, email_hint, cognito_sub)

            elif need_team_backfill:
                assert email_hint is not None  # narrows for mypy
                # Existing tenant from earlier partial auto-create has no
                # personal team / default_team_id. Backfill them in-place
                # so the next write call doesn't 400 with "no team_id".
                _log.info(
                    "tenant_team_backfilled_for_localdev",
                    cognito_sub=cognito_sub,
                )
                await _create_personal_team_and_assign(
                    conn, row["tenant_id"], email_hint, cognito_sub
                )

            if need_oauth_client or need_full_tenant or need_team_backfill:
                # Re-run the lookup so we return the same shape as the normal path.
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

    def __init__(self, pool: asyncpg.Pool) -> None:
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
        except Exception as exc:
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
        token = auth[len("Bearer ") :].strip()
        if not token:
            return _unauthorized(resource_metadata_url, "missing_token", "empty token")

        try:
            claims = await validator.validate(token)
        except JwtError as exc:
            return _unauthorized(resource_metadata_url, "invalid_token", exc.code)

        # email_hint passes through to the (optional) localdev auto-create path
        # in DbTenantResolver; ignored in prod where auto_create_for_localdev=False.
        email_hint = claims.raw.get("email") or claims.raw.get("username")
        resolution = await resolver.resolve(
            claims.sub,
            claims.client_id,
            email_hint=email_hint if isinstance(email_hint, str) else None,
        )

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
            scopes=frozenset(s.rsplit("/", 1)[-1] for s in claims.scopes),
        )

        # Fire-and-forget last_seen / last_used update
        # (asyncio.create_task is safe inside an async middleware running on the loop)
        # TODO(T-5.12): add audit log call here via mem_mcp.audit.logger
        task = asyncio.create_task(touch.touch(resolution.identity_id, claims.client_id))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

        return await call_next(request)

    return bearer_middleware


# --------------------------------------------------------------------------
# Response helpers
# --------------------------------------------------------------------------


def _www_authenticate(resource_metadata_url: str, error: str, error_description: str) -> str:
    """Build RFC 6750 §3 WWW-Authenticate header value.

    Per RFC 9728, ``resource_metadata`` should be the URL of the
    Protected-Resource-Metadata document itself, not the resource root.
    If the caller passed just the resource host, append the well-known
    path so OAuth-discovery clients (Claude Code, claude.ai) GET the
    JSON document instead of hitting the resource root (which on docker
    compose routes to the Next.js web service and returns HTML).
    """
    metadata_url = resource_metadata_url
    if "/.well-known/oauth-protected-resource" not in metadata_url:
        metadata_url = metadata_url.rstrip("/") + "/.well-known/oauth-protected-resource"
    return (
        f'Bearer realm="mem-mcp", '
        f'resource_metadata="{metadata_url}", '
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
