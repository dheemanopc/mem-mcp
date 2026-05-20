"""Tests for embedding_status column and embed_or_skip refactor (T-x.x.x)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from mem_mcp.embeddings.bedrock import EmbeddingError, EmbedResult
from mem_mcp.embeddings.embed_or_skip import embed_or_skip


class TestEmbedOrSkip:
    """Tests for embed_or_skip with new embedding_status return value."""

    @pytest.mark.asyncio
    async def test_returns_ok_on_bedrock_success(self) -> None:
        """embed_or_skip returns (vector, tokens, 'ok') on successful embedding."""
        embedder = AsyncMock()
        embedder.embed.return_value = EmbedResult(
            vector=[0.1, 0.2, 0.3], input_tokens=42
        )

        vec, tokens, status = await embed_or_skip(
            content="hello world",
            indexable=True,
            embedder=embedder,
        )

        assert vec == [0.1, 0.2, 0.3]
        assert tokens == 42
        assert status == "ok"

    @pytest.mark.asyncio
    async def test_returns_skipped_opt_out_when_indexable_false(self) -> None:
        """embed_or_skip returns (None, 0, 'skipped_opt_out') when indexable=False."""
        embedder = AsyncMock()

        vec, tokens, status = await embed_or_skip(
            content="hello",
            indexable=False,
            embedder=embedder,
        )

        assert vec is None
        assert tokens == 0
        assert status == "skipped_opt_out"
        embedder.embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_skipped_oversize_when_content_too_large(self) -> None:
        """embed_or_skip returns (None, 0, 'skipped_oversize') for content > 32KB."""
        embedder = AsyncMock()
        large_content = "x" * 33_000

        vec, tokens, status = await embed_or_skip(
            content=large_content,
            indexable=True,
            embedder=embedder,
        )

        assert vec is None
        assert tokens == 0
        assert status == "skipped_oversize"
        embedder.embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_failed_throttled_on_bedrock_throttle(self) -> None:
        """embed_or_skip returns (None, 0, 'failed_throttled') on throttle."""
        embedder = AsyncMock()
        embedder.embed.side_effect = EmbeddingError(
            "throttled", "rate limited", retry_after_seconds=60
        )

        vec, tokens, status = await embed_or_skip(
            content="hello",
            indexable=True,
            embedder=embedder,
        )

        assert vec is None
        assert tokens == 0
        assert status == "failed_throttled"

    @pytest.mark.asyncio
    async def test_returns_failed_unavailable_on_bedrock_unavailable(self) -> None:
        """embed_or_skip returns (None, 0, 'failed_unavailable') on 503."""
        embedder = AsyncMock()
        embedder.embed.side_effect = EmbeddingError(
            "unavailable", "service unavailable"
        )

        vec, tokens, status = await embed_or_skip(
            content="hello",
            indexable=True,
            embedder=embedder,
        )

        assert vec is None
        assert tokens == 0
        assert status == "failed_unavailable"

    @pytest.mark.asyncio
    async def test_raises_on_invalid_input(self) -> None:
        """embed_or_skip RAISES EmbeddingError on invalid_input (behavior change)."""
        embedder = AsyncMock()
        embedder.embed.side_effect = EmbeddingError(
            "invalid_input", "malformed UTF-8"
        )

        with pytest.raises(EmbeddingError) as exc_info:
            await embed_or_skip(
                content="bad \x80 input",
                indexable=True,
                embedder=embedder,
            )

        assert exc_info.value.code == "invalid_input"

    @pytest.mark.asyncio
    async def test_returns_failed_unknown_on_unexpected_error(self) -> None:
        """embed_or_skip returns (None, 0, 'failed_unknown') on unexpected exception."""
        embedder = AsyncMock()
        embedder.embed.side_effect = RuntimeError("unexpected error")

        vec, tokens, status = await embed_or_skip(
            content="hello",
            indexable=True,
            embedder=embedder,
        )

        assert vec is None
        assert tokens == 0
        assert status == "failed_unknown"
