"""Async asyncpg pool lifecycle for mem-mcp.

Single-process invariant: ``init_pool()`` is called once from FastAPI's
lifespan handler (T-3.5). Tests skip the real pool entirely — see
``tests/unit/test_tenant_tx.py``.
"""

from __future__ import annotations

import json

import asyncpg  # type: ignore[import-untyped]

from mem_mcp.config import get_settings


async def _init_connection(conn: asyncpg.Connection) -> None:
    # pgvector text codec — vector columns ship as e.g. "[0.1,0.2,...]"
    await conn.set_type_codec(
        "vector",
        encoder=lambda v: "[" + ",".join(map(str, v)) + "]",
        decoder=lambda s: [float(x) for x in s[1:-1].split(",")],
        schema="public",
        format="text",
    )
    # JSON / JSONB codec — without this, asyncpg returns JSONB columns as raw
    # str instead of parsed dict, which makes Pydantic models with
    # `metadata: dict[str, Any]` (memory_get, memory_thread_get, memory_list)
    # fail validation and surface as JsonRpcError(-32603, "internal error").
    # See bug report fed1023c-d715-4b15-96e1-e2a47b8deb5e.
    for typename in ("jsonb", "json"):
        await conn.set_type_codec(
            typename,
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )


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
        init=_init_connection,
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
