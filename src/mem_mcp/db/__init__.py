"""mem_mcp database access layer.

Public API:
    init_pool(dsn=None) -> asyncpg.Pool   — call once at startup
    close_pool() -> None                  — call at shutdown
    get_pool() -> asyncpg.Pool            — accessor; RuntimeError if not initialized
    tenant_tx(pool, tenant_id)            — async ctx manager; SET LOCAL app.current_tenant_id
    system_tx(pool)                       — async ctx manager; mem_maint role; no tenant set
"""

from __future__ import annotations

from mem_mcp.db.pool import close_pool, get_pool, init_pool
from mem_mcp.db.tenant_tx import system_tx, tenant_tx

__all__ = [
    "close_pool",
    "get_pool",
    "init_pool",
    "system_tx",
    "tenant_tx",
]
