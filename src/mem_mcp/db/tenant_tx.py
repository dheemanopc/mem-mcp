"""Tenant-scoped and system-scoped DB transactions.

The mem-mcp tenant isolation contract (LLD §5.2, spec §5.2):
    - ``tenant_tx(pool, tenant_id)`` opens a transaction and sets the
      session-LOCAL setting ``app.current_tenant_id`` to the UUID. RLS
      policies on ``memories`` and ``tenant_daily_usage`` filter rows by
      this setting.
    - The setting MUST be ``LOCAL`` (the third arg to set_config is True)
      so it never leaks across pool acquisitions.
    - ``system_tx(pool)`` is for maintenance jobs running as ``mem_maint``
      (which has BYPASSRLS); does NOT set tenant.

Per spec §5.3.2: no code path may call ``SET app.current_tenant_id``
without ``LOCAL``. There is no public function for that — only this
module ever sets the variable.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg


@contextlib.asynccontextmanager
async def tenant_tx(
    pool: "asyncpg.Pool", tenant_id: UUID
) -> AsyncIterator["asyncpg.Connection"]:
    """Acquire a connection, open a transaction, set tenant context, yield.

    Usage:
        async with tenant_tx(pool, tenant_id) as conn:
            await conn.fetch("SELECT id FROM memories")
            # RLS automatically scopes results to this tenant.

    The ``set_config(..., true)`` call uses LOCAL scope, so the tenant
    context is discarded when the transaction commits or rolls back.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            yield conn


@contextlib.asynccontextmanager
async def system_tx(pool: "asyncpg.Pool") -> AsyncIterator["asyncpg.Connection"]:
    """Acquire a connection for maintenance / system work (mem_maint role).

    Does NOT set ``app.current_tenant_id`` — callers operate against the
    full table without tenant scoping. Use only from ``mem_mcp.jobs.*``.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn
