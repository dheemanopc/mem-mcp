"""Async asyncpg pool lifecycle for mem-mcp.

Single-process invariant: ``init_pool()`` is called once from FastAPI's
lifespan handler (T-3.5). Tests skip the real pool entirely — see
``tests/unit/test_tenant_tx.py``.
"""

from __future__ import annotations

import asyncpg  # type: ignore[import-untyped]

from mem_mcp.config import get_settings

_pool: asyncpg.Pool | None = None


async def init_pool(dsn: str | None = None) -> asyncpg.Pool:
    """Create the global asyncpg pool. Idempotent."""
    global _pool
    if _pool is not None:
        return _pool

    if dsn is None:
        dsn = get_settings().db_dsn

    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=2,
        max_size=10,
        command_timeout=30,
        server_settings={"application_name": "mem-mcp"},
    )
    return _pool


async def close_pool() -> None:
    """Close the global pool and clear the reference. Idempotent."""
    global _pool
    if _pool is None:
        return
    await _pool.close()
    _pool = None


def get_pool() -> asyncpg.Pool:
    """Return the global pool. Raises ``RuntimeError`` if not initialized."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_pool() first.")
    return _pool


def _reset_for_tests() -> None:
    """Test-only: clear the global pool reference (does NOT close)."""
    global _pool
    _pool = None
