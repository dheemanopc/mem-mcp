"""Regression: standalone job pools must register the pgvector codec.

chunk_build and embedding_backfill build their own asyncpg pools (not the
app's init_pool). Both write `vector` columns, so their pools MUST pass
init=_init_connection — without it asyncpg rejects a Python list bound to a
vector column ("expected str, got list") and every vector write fails. This
silently broke multi-chunk chunking and embedding backfill in production.
"""

from __future__ import annotations

import asyncpg
import pytest

from mem_mcp.db.pool import _init_connection
from mem_mcp.jobs import chunk_build, embedding_backfill


class _StopCreatePoolError(Exception):
    pass


class _StubSettings:
    log_level = "INFO"
    db_maint_dsn_asyncpg = "postgresql://u:p@localhost/db"


async def _assert_codec_wired(monkeypatch, module) -> None:
    captured: dict = {}

    async def fake_create_pool(**kwargs):
        captured.update(kwargs)
        raise _StopCreatePoolError  # abort main() right after pool construction

    monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(module, "get_settings", lambda: _StubSettings())

    with pytest.raises(_StopCreatePoolError):
        await module.main()

    assert captured.get("init") is _init_connection


@pytest.mark.asyncio
async def test_chunk_build_pool_registers_vector_codec(monkeypatch) -> None:
    await _assert_codec_wired(monkeypatch, chunk_build)


@pytest.mark.asyncio
async def test_embedding_backfill_pool_registers_vector_codec(monkeypatch) -> None:
    await _assert_codec_wired(monkeypatch, embedding_backfill)
