"""OAuth client revocation helpers (T-7.13).

Manages revoking OAuth clients: marks oauth_clients.disabled=true
and calls Cognito DeleteUserPoolClient (best-effort; failures are
logged and retried by cleanup_clients job).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from mem_mcp.db.tenant_tx import system_tx

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


# --------------------------------------------------------------------------
# Protocols (test seams)
# --------------------------------------------------------------------------


class CognitoClientDeleter(Protocol):
    """Wraps Cognito DeleteUserPoolClient."""

    async def delete_user_pool_client(self, client_id: str) -> None: ...


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class RevokeClientError(Exception):
    """Raised by revoke_client. Web layer maps code → HTTP."""

    def __init__(self, code: str) -> None:
        self.code = code  # 'not_found', 'already_disabled', 'wrong_tenant'
        super().__init__(f"revoke error: {code}")


# --------------------------------------------------------------------------
# Main function
# --------------------------------------------------------------------------


async def revoke_client(
    pool: "asyncpg.Pool",  # noqa: UP037
    *,
    tenant_id: UUID,
    client_id: str,
    deleter: CognitoClientDeleter,
    audit: Any,  # AuditLogger Protocol
    request_id: str,
) -> None:
    """Mark oauth_clients.disabled=true and delete from Cognito.

    Per FR-12.3.8.2:
      1. SELECT oauth_clients WHERE id=$1 — if missing → RevokeClientError('not_found')
      2. If row.tenant_id != tenant_id → RevokeClientError('wrong_tenant') (prevents cross-tenant revoke)
      3. If row.disabled is already true → RevokeClientError('already_disabled') (idempotency: caller can decide to ignore)
      4. UPDATE oauth_clients SET disabled=true WHERE id=$1
      5. Best-effort: deleter.delete_user_pool_client(client_id). Catch + log Cognito errors but don't bail.
         (The DB row stays disabled; cleanup_clients job will retry the Cognito delete eventually.)
      6. audit('oauth.client_revoked', target_id=client_id, target_kind='oauth_client',
              tenant_id=tenant_id, request_id=request_id)

    Use system_tx (oauth_clients has no RLS — it's keyed by id, lookups are admin-context).
    Tenant ownership check is done in Python after the SELECT.
    """
    async with system_tx(pool) as conn:
        # 1. Fetch the row
        row = await conn.fetchrow(
            "SELECT id, tenant_id, disabled FROM oauth_clients WHERE id = $1", client_id
        )

        if row is None:
            raise RevokeClientError("not_found")

        # 2. Check tenant ownership
        if row["tenant_id"] != tenant_id:
            raise RevokeClientError("wrong_tenant")

        # 3. Check if already disabled
        if row["disabled"] is True:
            raise RevokeClientError("already_disabled")

        # 4. Mark as disabled
        await conn.execute("UPDATE oauth_clients SET disabled = true WHERE id = $1", client_id)

        # 5. Best-effort Cognito deletion
        try:
            await deleter.delete_user_pool_client(client_id)
        except Exception:
            # Swallow exceptions — cleanup_clients job will retry
            pass

        # 6. Audit the revocation
        await audit.audit(
            conn,
            action="oauth.client_revoked",
            result="success",
            tenant_id=tenant_id,
            target_id=client_id,
            target_kind="oauth_client",
            request_id=request_id,
        )
