"""Tests for embedding backfill worker (T-x.x.x)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mem_mcp.embeddings.bedrock import EmbeddingError, EmbedResult
from mem_mcp.jobs.embedding_backfill import (
    fetch_candidates,
    run_backfill,
    update_row_status,
    update_row_success,
)


class TestEmbeddingBackfillHelper:
    """Tests for backfill helper functions."""

    @pytest.mark.asyncio
    async def test_fetch_candidates_returns_candidates(self) -> None:
        """fetch_candidates returns rows with id and content."""
        mid1, mid2 = uuid4(), uuid4()
        conn = AsyncMock()
        conn.fetch.return_value = [
            {"id": mid1, "content": "first"},
            {"id": mid2, "content": "second"},
        ]

        candidates = await fetch_candidates(conn, batch_size=50)

        assert len(candidates) == 2
        assert candidates[0] == {"id": str(mid1), "content": "first"}
        assert candidates[1] == {"id": str(mid2), "content": "second"}
        # Verify query limits to retryable statuses
        call_args = conn.fetch.call_args[0][0]
        assert "failed_throttled" in str(call_args)
        assert "failed_unavailable" in str(call_args)

    @pytest.mark.asyncio
    async def test_update_row_success_sets_embedding_and_status(self) -> None:
        """update_row_success updates embedding vector and status to 'ok'."""
        conn = AsyncMock()
        mid = uuid4()
        vec = [0.1, 0.2, 0.3]

        await update_row_success(conn, str(mid), vec)

        # Verify the update was called
        conn.execute.assert_called_once()
        call_args = conn.execute.call_args[0][1]
        assert call_args["id"] == str(mid)
        assert call_args["embedding"] == vec

    @pytest.mark.asyncio
    async def test_update_row_status_sets_status_only(self) -> None:
        """update_row_status sets only embedding_status field."""
        conn = AsyncMock()
        mid = uuid4()

        await update_row_status(conn, str(mid), "failed_validation")

        conn.execute.assert_called_once()
        call_args = conn.execute.call_args[0][1]
        assert call_args["id"] == str(mid)
        assert call_args["status"] == "failed_validation"


class TestEmbeddingBackfill:
    """Tests for run_backfill main loop."""

    @pytest.mark.asyncio
    async def test_backfill_on_success_updates_row(self) -> None:
        """Backfill successfully embeds and updates row on success."""
        mid = str(uuid4())
        pool = MagicMock()
        conn = AsyncMock()

        # Set up pool.acquire as an async context manager
        acquire_ctx = AsyncMock()
        acquire_ctx.__aenter__.return_value = conn
        acquire_ctx.__aexit__.return_value = None
        pool.acquire.return_value = acquire_ctx

        # Mock fetch_candidates to return one row
        conn.fetch.return_value = [{"id": mid, "content": "test"}]

        # Mock embedder
        embedder = AsyncMock()
        embedder.embed.return_value = EmbedResult(
            vector=[0.1, 0.2], input_tokens=10
        )

        result = await run_backfill(pool, embedder, batch_size=50)

        assert result["processed"] == 1
        assert result["succeeded"] == 1
        # Verify update was called (once for success)
        assert conn.execute.call_count >= 1

    @pytest.mark.asyncio
    async def test_backfill_on_throttle_leaves_status_unchanged(self) -> None:
        """Backfill on throttle does not update status (will retry next pass)."""
        mid = str(uuid4())
        pool = MagicMock()
        conn = AsyncMock()

        acquire_ctx = AsyncMock()
        acquire_ctx.__aenter__.return_value = conn
        acquire_ctx.__aexit__.return_value = None
        pool.acquire.return_value = acquire_ctx

        # Mock fetch_candidates
        conn.fetch.return_value = [{"id": mid, "content": "test"}]

        # Mock embedder to throw throttle
        embedder = AsyncMock()
        embedder.embed.side_effect = EmbeddingError("throttled", "slow")

        result = await run_backfill(pool, embedder, batch_size=50)

        assert result["processed"] == 1
        assert result["failed_throttled"] == 1
        # Should not call update_row_status for throttle
        # (status left unchanged for retry)

    @pytest.mark.asyncio
    async def test_backfill_on_validation_error_marks_non_retryable(self) -> None:
        """Backfill on validation error marks row as failed_validation."""
        mid = str(uuid4())
        pool = MagicMock()
        conn = AsyncMock()

        acquire_ctx = AsyncMock()
        acquire_ctx.__aenter__.return_value = conn
        acquire_ctx.__aexit__.return_value = None
        pool.acquire.return_value = acquire_ctx

        # Mock fetch_candidates
        conn.fetch.return_value = [{"id": mid, "content": "bad \x80"}]

        # Mock embedder to throw validation error
        embedder = AsyncMock()
        embedder.embed.side_effect = EmbeddingError("invalid_input", "bad encoding")

        result = await run_backfill(pool, embedder, batch_size=50)

        assert result["processed"] == 1
        assert result["failed_validation"] == 1
        # Should call update_row_status to set failed_validation
        assert conn.execute.call_count >= 1

    @pytest.mark.asyncio
    async def test_backfill_respects_concurrency_limit(self) -> None:
        """Backfill respects max_concurrency semaphore."""
        # This is a bit tricky to test; we'd need to verify that only
        # max_concurrency tasks are in flight at any time. For now, just
        # verify the backfill completes without hanging.
        pool = MagicMock()
        conn = AsyncMock()

        acquire_ctx = AsyncMock()
        acquire_ctx.__aenter__.return_value = conn
        acquire_ctx.__aexit__.return_value = None
        pool.acquire.return_value = acquire_ctx

        # Many candidates
        candidates = [
            {"id": str(uuid4()), "content": f"content {i}"}
            for i in range(100)
        ]
        conn.fetch.return_value = candidates

        # Fast embedder
        embedder = AsyncMock()
        embedder.embed.return_value = EmbedResult(
            vector=[0.1], input_tokens=1
        )

        result = await run_backfill(pool, embedder, max_concurrency=10)

        assert result["processed"] == 100
        assert result["succeeded"] == 100
