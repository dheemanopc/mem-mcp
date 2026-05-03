"""Account closure lifecycle management (T-7.12).

Manages three phases of account closure:
- request_closure: mark pending_deletion, generate cancel token, sign out identities
- cancel_closure: revert to active within 24h window
- finalize_closure: delete all data, anonymize tenants row (after 24h grace)
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from mem_mcp.db.tenant_tx import system_tx

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


# --------------------------------------------------------------------------
# Errors & Results
# --------------------------------------------------------------------------


class ClosureError(Exception):
    """Raised by closure operations. Web layer maps to HTTP."""

    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code  # 'not_pending', 'token_invalid', 'expired', 'not_found', 'already_pending', 'not_active'
        self.detail = detail
        super().__init__(f"closure error: {code}")


@dataclass(frozen=True)
class ClosureRequestResult:
    cancel_token: str  # raw token to email user
    cancel_until: datetime  # now + 24h
    identities_signed_out: int


# --------------------------------------------------------------------------
# Cognito Protocol seams
# --------------------------------------------------------------------------


class CognitoGlobalSignOutter(Protocol):
    """Wraps AdminUserGlobalSignOut for a cognito_username."""

    async def admin_user_global_sign_out(self, cognito_username: str) -> None: ...


class CognitoClientDeleter(Protocol):
    """Wraps DeleteUserPoolClient for an oauth_clients.id (which equals cognito_client_id)."""

    async def delete_user_pool_client(self, client_id: str) -> None: ...


class CognitoAdminDeleter(Protocol):
    """Wraps AdminDeleteUser for a cognito_username."""

    async def admin_delete_user(self, cognito_username: str) -> None: ...


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _hash_token(token: str) -> str:
    """Compute SHA256 hash of cancel token."""
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------------
# Main functions
# --------------------------------------------------------------------------


async def request_closure(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    sign_outter: CognitoGlobalSignOutter,
    audit: Any,  # AuditLogger Protocol
    request_id: str,
    now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
) -> ClosureRequestResult:
    """Mark account pending_deletion, generate cancel token, sign out all identities.

    1. SELECT tenants WHERE id=$1 — if status != 'active', raise ClosureError
    2. Generate cancel_token = secrets.token_urlsafe(32); compute sha256(token)
    3. UPDATE tenants SET status='pending_deletion', deletion_requested_at=now(), deletion_cancel_token_hash=$
    4. SELECT all tenant_identities for the tenant
    5. For each identity, call sign_outter.admin_user_global_sign_out(cognito_username) — best-effort
    6. audit('tenant.deletion_requested', ...)
    7. Return ClosureRequestResult with raw cancel_token and 24h cancel_until

    Raises ClosureError('already_pending' | 'not_active' | 'not_found') if tenant status is not 'active'.
    """
    async with system_tx(pool) as conn:
        # Fetch tenant
        tenant_row = await conn.fetchrow(
            "SELECT id, status FROM tenants WHERE id = $1",
            tenant_id,
        )

        if tenant_row is None:
            raise ClosureError("not_found", detail="Tenant not found")

        if tenant_row["status"] == "pending_deletion":
            raise ClosureError("already_pending", detail="Account closure already requested")

        if tenant_row["status"] != "active":
            raise ClosureError("not_active", detail=f"Tenant status is {tenant_row['status']}")

        # Generate cancel token
        cancel_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(cancel_token)
        now_ts = now()
        cancel_until = now_ts + timedelta(hours=24)

        # Update tenants status
        await conn.execute(
            """
            UPDATE tenants
            SET status = 'pending_deletion',
                deletion_requested_at = $2,
                deletion_cancel_token_hash = $3
            WHERE id = $1
            """,
            tenant_id,
            now_ts,
            token_hash,
        )

        # Fetch all identities for sign-out
        identity_rows = await conn.fetch(
            "SELECT cognito_username FROM tenant_identities WHERE tenant_id = $1",
            tenant_id,
        )

    # Sign out each identity (best-effort, outside transaction)
    signed_out_count = 0
    for identity_row in identity_rows:
        cognito_username = identity_row["cognito_username"]
        if cognito_username:
            try:
                await sign_outter.admin_user_global_sign_out(cognito_username)
                signed_out_count += 1
            except Exception as exc:
                audit.log_error(
                    f"Failed to sign out identity {cognito_username}",
                    exc_info=exc,
                    request_id=request_id,
                )

    # Audit
    await audit.log_event(
        "tenant.deletion_requested",
        target_id=tenant_id,
        details={
            "identities_signed_out": signed_out_count,
            "cancel_until": cancel_until.isoformat(),
        },
        request_id=request_id,
    )

    return ClosureRequestResult(
        cancel_token=cancel_token,
        cancel_until=cancel_until,
        identities_signed_out=signed_out_count,
    )


async def cancel_closure(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    cancel_token: str,
    audit: Any,  # AuditLogger Protocol
    request_id: str,
    now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
) -> None:
    """Revert pending_deletion to active if token is valid and within 24h.

    1. SELECT tenant; status MUST be 'pending_deletion' else ClosureError('not_pending')
    2. Compute sha256(cancel_token); compare with stored hash via hmac.compare_digest
    3. If now() - deletion_requested_at > 24h, raise ClosureError('expired')
    4. UPDATE tenants SET status='active', deletion_requested_at=NULL, deletion_cancel_token_hash=NULL
    5. audit('tenant.deletion_cancelled', ...)

    Raises ClosureError('not_pending' | 'token_invalid' | 'expired').
    """
    async with system_tx(pool) as conn:
        # Fetch tenant
        tenant_row = await conn.fetchrow(
            "SELECT id, status, deletion_requested_at, deletion_cancel_token_hash FROM tenants WHERE id = $1",
            tenant_id,
        )

        if tenant_row is None:
            raise ClosureError("not_found", detail="Tenant not found")

        if tenant_row["status"] != "pending_deletion":
            raise ClosureError("not_pending", detail="Account closure not requested")

        # Validate token
        token_hash = _hash_token(cancel_token)
        stored_hash = tenant_row["deletion_cancel_token_hash"]
        if not hmac.compare_digest(token_hash, stored_hash or ""):
            raise ClosureError("token_invalid", detail="Cancel token is invalid")

        # Check time window
        deletion_requested_at = tenant_row["deletion_requested_at"]
        if deletion_requested_at is None:
            raise ClosureError("expired", detail="Deletion request timestamp missing")

        elapsed = now() - deletion_requested_at
        if elapsed.total_seconds() > 86400:  # 24 hours
            raise ClosureError("expired", detail="24-hour cancellation window has expired")

        # Revert to active
        await conn.execute(
            """
            UPDATE tenants
            SET status = 'active',
                deletion_requested_at = NULL,
                deletion_cancel_token_hash = NULL
            WHERE id = $1
            """,
            tenant_id,
        )

    # Audit
    await audit.log_event(
        "tenant.deletion_cancelled",
        target_id=tenant_id,
        request_id=request_id,
    )


async def finalize_closure(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    deleter: CognitoAdminDeleter,
    sign_outter: CognitoGlobalSignOutter,  # may be unused
    client_deleter: CognitoClientDeleter,
    audit: Any,  # AuditLogger Protocol
    request_id: str,
) -> None:
    """Finalize account closure: delete data, anonymize tenants row.

    Called by the retention job after 24h grace expires.

    1. Audit 'tenant.deleted' FIRST (with tenant_id still valid)
    2. DELETE child rows (memories, tenant_daily_usage, tenant_identities, oauth_consents,
       oauth_clients, web_sessions, link_state, feedback) — explicit in dependency order
    3. For each tenant_identity, call deleter.admin_delete_user(cognito_username) (best-effort)
    4. For each oauth_client, client_deleter.delete_user_pool_client(client_id) (best-effort)
    5. UPDATE tenants SET status='deleted', email='deleted-<uuid>@removed.local', display_name=NULL,
       deletion_requested_at=NULL, deletion_cancel_token_hash=NULL, metadata='{}'
    """
    async with system_tx(pool) as conn:
        # Fetch tenant to verify it exists
        tenant_row = await conn.fetchrow(
            "SELECT id, email FROM tenants WHERE id = $1",
            tenant_id,
        )

        if tenant_row is None:
            raise ClosureError("not_found", detail="Tenant not found")

        # Audit FIRST (while tenant_id is still meaningful)
        await audit.log_event(
            "tenant.deleted",
            target_id=tenant_id,
            request_id=request_id,
        )

        # Fetch identities and clients for Cognito cleanup
        identity_rows = await conn.fetch(
            "SELECT cognito_username FROM tenant_identities WHERE tenant_id = $1",
            tenant_id,
        )

        client_rows = await conn.fetch(
            "SELECT id FROM oauth_clients WHERE tenant_id = $1 AND deleted_at IS NULL",
            tenant_id,
        )

        # Delete child rows in dependency order
        await conn.execute("DELETE FROM memories WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM tenant_daily_usage WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM oauth_consents WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM web_sessions WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM link_state WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM feedback WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM oauth_clients WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM tenant_identities WHERE tenant_id = $1", tenant_id)

        # Anonymize tenants row
        anonymized_email = f"deleted-{tenant_id}@removed.local"
        await conn.execute(
            """
            UPDATE tenants
            SET status = 'deleted',
                email = $2,
                display_name = NULL,
                deletion_requested_at = NULL,
                deletion_cancel_token_hash = NULL,
                metadata = '{}'::jsonb
            WHERE id = $1
            """,
            tenant_id,
            anonymized_email,
        )

    # Cognito cleanup (best-effort, outside transaction)
    for identity_row in identity_rows:
        cognito_username = identity_row["cognito_username"]
        if cognito_username:
            try:
                await deleter.admin_delete_user(cognito_username)
            except Exception as exc:
                audit.log_error(
                    f"Failed to delete Cognito user {cognito_username}",
                    exc_info=exc,
                    request_id=request_id,
                )

    for client_row in client_rows:
        client_id = client_row["id"]
        try:
            await client_deleter.delete_user_pool_client(client_id)
        except Exception as exc:
            audit.log_error(
                f"Failed to delete OAuth client {client_id}",
                exc_info=exc,
                request_id=request_id,
            )
