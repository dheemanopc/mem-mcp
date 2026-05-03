"""Identity unlinking helpers (T-7.11).

Manages identity unlinking and primary identity promotion.
- unlink_identity: Delete identity + Cognito user
- promote_primary: Atomically swap is_primary flag
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from mem_mcp.db.tenant_tx import tenant_tx

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


class CognitoAdminDeleter(Protocol):
    """Wraps Cognito's AdminDeleteUser API call."""

    async def admin_delete_user(self, cognito_username: str) -> None: ...


class UnlinkingError(Exception):
    """Raised when unlinking fails. code discriminates the failure mode."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


class PromotionError(Exception):
    """Raised when promotion fails. code discriminates the failure mode."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


async def unlink_identity(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    identity_id: UUID,
    deleter: CognitoAdminDeleter,
    audit: Any,  # AuditLogger Protocol
    request_id: str,
) -> None:
    """Delete tenant_identities row + Cognito user. Audit identity.unlinked.

    Raises UnlinkingError(code=...) for:
    - 'last_identity': cannot unlink the only identity for the tenant
    - 'is_primary': cannot unlink the primary; promote another first
    - 'not_found': identity_id not in tenant_identities for this tenant
    """
    async with tenant_tx(pool, tenant_id) as conn:
        # Check identity count
        count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM tenant_identities
            WHERE tenant_id = $1
            """,
            tenant_id,
        )

        if count == 1:
            raise UnlinkingError("last_identity", "Cannot unlink the only identity")

        # Fetch the identity
        identity_row = await conn.fetchrow(
            """
            SELECT id, cognito_username, is_primary
            FROM tenant_identities
            WHERE id = $1 AND tenant_id = $2
            """,
            identity_id,
            tenant_id,
        )

        if identity_row is None:
            raise UnlinkingError("not_found", "Identity not found")

        if identity_row["is_primary"]:
            raise UnlinkingError("is_primary", "Cannot unlink primary identity")

        cognito_username = identity_row["cognito_username"]

        # DELETE from database
        await conn.execute(
            """
            DELETE FROM tenant_identities
            WHERE id = $1 AND tenant_id = $2
            """,
            identity_id,
            tenant_id,
        )

        # Call Cognito to delete user
        await deleter.admin_delete_user(cognito_username)

        # Audit
        await audit.audit(
            conn,
            action="identity.unlinked",
            result="success",
            tenant_id=tenant_id,
            identity_id=identity_id,
            request_id=request_id,
            details={"cognito_username": cognito_username},
        )


async def promote_primary(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    identity_id: UUID,
    audit: Any,  # AuditLogger Protocol
    request_id: str,
) -> None:
    """Make this identity the primary; demote the previous primary.

    Atomic: in a single tx, UPDATE all tenant_identities SET is_primary=false
    WHERE tenant_id=$1, then UPDATE the target row SET is_primary=true.
    The unique partial index `idx_identities_one_primary` enforces invariant.

    Raises PromotionError(code=...) for:
    - 'not_found'
    - 'already_primary': idempotent; raise for clarity
    """
    async with tenant_tx(pool, tenant_id) as conn:
        # Fetch the target identity
        identity_row = await conn.fetchrow(
            """
            SELECT id, is_primary
            FROM tenant_identities
            WHERE id = $1 AND tenant_id = $2
            """,
            identity_id,
            tenant_id,
        )

        if identity_row is None:
            raise PromotionError("not_found", "Identity not found")

        if identity_row["is_primary"]:
            raise PromotionError("already_primary", "Identity is already primary")

        # Atomically demote all, then promote target
        await conn.execute(
            """
            UPDATE tenant_identities
            SET is_primary = false
            WHERE tenant_id = $1
            """,
            tenant_id,
        )

        await conn.execute(
            """
            UPDATE tenant_identities
            SET is_primary = true
            WHERE id = $1 AND tenant_id = $2
            """,
            identity_id,
            tenant_id,
        )

        # Audit
        await audit.audit(
            conn,
            action="identity.linked",  # Use same action as linking for consistency
            result="success",
            tenant_id=tenant_id,
            identity_id=identity_id,
            request_id=request_id,
            details={"promoted_to_primary": True},
        )
