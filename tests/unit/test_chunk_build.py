"""Tests for the chunk-build worker's failure backoff / terminal state.

Regression coverage for the runaway fixed in migration 0039: an embed failure
must record backoff/terminal state (so the memory stops being re-selected every
pass) and a successful build must clear that state.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mem_mcp.embeddings.bedrock import EmbeddingError
from mem_mcp.jobs.chunk_build import (
    MAX_ATTEMPTS,
    build_chunks_for_memory,
    fetch_candidates,
    record_failure,
)


def _mock_pool() -> tuple[MagicMock, AsyncMock]:
    """Return (pool, conn) where pool.acquire() and conn.transaction() are
    async context managers yielding the same conn."""
    pool = MagicMock()
    conn = AsyncMock()

    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__.return_value = conn
    acquire_ctx.__aexit__.return_value = None
    pool.acquire.return_value = acquire_ctx

    tx = AsyncMock()
    tx.__aenter__.return_value = None
    tx.__aexit__.return_value = None
    conn.transaction = MagicMock(return_value=tx)
    return pool, conn


def _executed_sql(conn: AsyncMock) -> list[str]:
    return [str(c.args[0]) for c in conn.execute.call_args_list]


class TestCandidateQuery:
    @pytest.mark.asyncio
    async def test_candidates_sql_excludes_backed_off_rows(self) -> None:
        """The candidate SELECT joins chunk_build_state and filters on
        terminal / next_attempt_at so failed memories drop out."""
        conn = AsyncMock()
        conn.fetch.return_value = []

        await fetch_candidates(conn, batch_size=50)

        sql = str(conn.fetch.call_args.args[0])
        assert "chunk_build_state" in sql
        assert "next_attempt_at" in sql
        assert "terminal" in sql


class TestRecordFailure:
    @pytest.mark.asyncio
    async def test_record_failure_passes_permanent_and_max_attempts(self) -> None:
        pool, conn = _mock_pool()
        mid = uuid4()

        await record_failure(pool, mid, permanent=True, error="boom")

        conn.execute.assert_awaited_once()
        args = conn.execute.call_args.args
        assert args[1] == mid  # memory_id
        assert args[2] is True  # permanent
        assert args[3] == MAX_ATTEMPTS  # terminal cap
        assert args[4] == "boom"  # error (truncated to 500)


class TestBuildFailurePath:
    @pytest.mark.asyncio
    async def test_invalid_input_records_terminal_failure(self) -> None:
        """A permanent (invalid_input) embed error terminalizes the memory."""
        pool, conn = _mock_pool()
        embedder = AsyncMock()
        embedder.embed.side_effect = EmbeddingError("invalid_input", "bad content")
        row = {
            "id": uuid4(),
            "content": "some content",
            "content_hash": "h",
            "tenant_id": uuid4(),
            "has_embedding": False,  # forces the embed path (no single-reuse)
        }

        result = await build_chunks_for_memory(pool, embedder, row)

        assert result == "failed_embed"
        # record_failure ran with permanent=True; no chunk rows inserted.
        rec = [c for c in conn.execute.call_args_list if "chunk_build_state" in str(c.args[0])]
        assert rec, "expected a chunk_build_state upsert"
        assert rec[0].args[2] is True  # permanent
        assert not any(
            "INSERT INTO memory_chunks" in str(c.args[0]) for c in conn.execute.call_args_list
        )

    @pytest.mark.asyncio
    async def test_throttled_records_transient_failure(self) -> None:
        """A transient (throttled) embed error backs off but is not terminal."""
        pool, conn = _mock_pool()
        embedder = AsyncMock()
        embedder.embed.side_effect = EmbeddingError("throttled", "slow down")
        row = {
            "id": uuid4(),
            "content": "some content",
            "content_hash": "h",
            "tenant_id": uuid4(),
            "has_embedding": False,
        }

        result = await build_chunks_for_memory(pool, embedder, row)

        assert result == "failed_embed"
        rec = [c for c in conn.execute.call_args_list if "chunk_build_state" in str(c.args[0])]
        assert rec, "expected a chunk_build_state upsert"
        assert rec[0].args[2] is False  # not permanent


class TestBuildSuccessPath:
    @pytest.mark.asyncio
    async def test_success_clears_state(self) -> None:
        """A successful single-reuse build deletes any prior backoff state."""
        pool, conn = _mock_pool()
        embedder = AsyncMock()  # must NOT be called on the single-reuse fast path
        row = {
            "id": uuid4(),
            "content": "short",
            "content_hash": "h",
            "tenant_id": uuid4(),
            "has_embedding": True,  # single chunk + embedding → fast path, no embed
        }

        result = await build_chunks_for_memory(pool, embedder, row)

        assert result == "built"
        embedder.embed.assert_not_called()
        sql = _executed_sql(conn)
        assert any("DELETE FROM chunk_build_state" in s for s in sql), sql
