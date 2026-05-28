"""Concrete PermissionResolver — wraps rbac.has_permission for plugin use."""

from __future__ import annotations

from uuid import UUID

import asyncpg  # type: ignore[import-untyped]

from mem_mcp.auth.permissions import Permission
from mem_mcp.auth.rbac import has_permission
from mem_mcp.db.tenant_tx import system_tx


class PermissionResolverImpl:
    """Tenant-bound permission resolver."""

    def __init__(self, pool: asyncpg.Pool, tenant_id: UUID) -> None:
        self._pool = pool
        self._tenant_id = tenant_id

    async def has(self, permission: Permission, *, team_id: UUID | None = None) -> bool:
        """Check if the tenant has a given permission (optionally scoped to a team)."""
        async with system_tx(self._pool) as conn:
            return await has_permission(conn, self._tenant_id, permission, team_id=team_id)
