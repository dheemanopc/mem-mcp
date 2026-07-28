"""Tests for deterministic content chunking + the chunk_build job."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mem_mcp.embeddings.bedrock import EmbedResult
from mem_mcp.jobs.chunk_build import build_chunks_for_memory, fetch_candidates, run_chunk_build
from mem_mcp.memory.chunking import (
    CHUNK_MAX_CHARS,
    CHUNK_TARGET_CHARS,
    MAX_CHUNKS_PER_MEMORY,
    split_into_chunks,
)

# --------------------------------------------------------------------------
# split_into_chunks
# --------------------------------------------------------------------------


class TestSplitIntoChunks:
    def test_empty_and_whitespace(self) -> None:
        assert split_into_chunks("") == []
        assert split_into_chunks("   \n\n  ") == []

    def test_short_content_single_chunk(self) -> None:
        assert split_into_chunks("a short note") == ["a short note"]

    def test_short_content_stripped(self) -> None:
        assert split_into_chunks("  padded  ") == ["padded"]

    def test_paragraphs_packed_to_target(self) -> None:
        para = "word " * 100  # ~500 chars
        content = "\n\n".join(para.strip() for _ in range(10))  # ~5000 chars
        chunks = split_into_chunks(content)
        assert len(chunks) > 1
        assert all(len(c) <= CHUNK_MAX_CHARS for c in chunks)
        # Packing should approach the target, not emit one paragraph per chunk
        assert len(chunks) < 10

    def test_oversize_paragraph_split_at_sentences(self) -> None:
        sentence = "This is a sentence about something. "
        content = sentence * 120  # single paragraph, ~4300 chars
        chunks = split_into_chunks(content)
        assert len(chunks) > 1
        assert all(len(c) <= CHUNK_MAX_CHARS for c in chunks)
        # No mid-sentence cuts: every chunk ends at a sentence boundary
        assert all(c.rstrip().endswith(".") for c in chunks)

    def test_oversize_sentence_hard_wrapped(self) -> None:
        content = "x" * (CHUNK_MAX_CHARS * 3)
        chunks = split_into_chunks(content)
        assert all(len(c) <= CHUNK_MAX_CHARS for c in chunks)
        assert "".join(chunks) == content  # nothing dropped

    def test_order_preserved(self) -> None:
        paras = [f"paragraph number {i} " + "pad " * 80 for i in range(8)]
        chunks = split_into_chunks("\n\n".join(paras))
        joined = "\n\n".join(chunks)
        positions = [joined.index(f"paragraph number {i}") for i in range(8)]
        assert positions == sorted(positions)

    def test_deterministic(self) -> None:
        content = ("alpha " * 300 + "\n\n") * 5
        assert split_into_chunks(content) == split_into_chunks(content)

    def test_max_chunks_cap(self) -> None:
        content = "\n\n".join("p" * CHUNK_TARGET_CHARS for _ in range(MAX_CHUNKS_PER_MEMORY + 50))
        assert len(split_into_chunks(content)) == MAX_CHUNKS_PER_MEMORY


# --------------------------------------------------------------------------
# chunk_build job
# --------------------------------------------------------------------------


class _FakeEmbedder:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def embed(self, text: str) -> EmbedResult:
        self.calls.append(text)
        if self.fail:
            raise ConnectionError("embed down")
        return EmbedResult(vector=[0.1] * 1024, input_tokens=5)


class _FakePool:
    """Pool whose acquire() yields a conn recording execute/fetch calls."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        conn = self._conn

        class _Ctx:
            async def __aenter__(self) -> Any:
                return conn

            async def __aexit__(self, *args: Any) -> None:
                return None

        return _Ctx()


def _conn_with_tx() -> AsyncMock:
    conn = AsyncMock()
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx)
    return conn


def _row(content: str, has_embedding: bool = True) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "content": content,
        "content_hash": "h" * 64,
        "tenant_id": uuid4(),
        "has_embedding": has_embedding,
    }


class TestChunkBuildJob:
    @pytest.mark.asyncio
    async def test_single_chunk_reuses_memory_embedding(self) -> None:
        """Short memory + existing embedding → SQL-side copy, no embed call."""
        conn = _conn_with_tx()
        embedder = _FakeEmbedder()
        result = await build_chunks_for_memory(_FakePool(conn), embedder, _row("short note"))
        assert result == "built"
        assert embedder.calls == []  # no embed call
        executed = [c.args[0] for c in conn.execute.call_args_list]
        assert any("DELETE FROM memory_chunks" in q for q in executed)
        assert any("SELECT id, tenant_id, 0," in q for q in executed)

    @pytest.mark.asyncio
    async def test_single_chunk_without_embedding_embeds(self) -> None:
        """Short memory whose embedding is NULL (e.g. degraded write) embeds."""
        conn = _conn_with_tx()
        embedder = _FakeEmbedder()
        result = await build_chunks_for_memory(
            _FakePool(conn), embedder, _row("short note", has_embedding=False)
        )
        assert result == "built"
        assert embedder.calls == ["short note"]

    @pytest.mark.asyncio
    async def test_multi_chunk_embeds_each(self) -> None:
        content = ("sentence about topic. " * 80 + "\n\n") * 4  # several chunks
        conn = _conn_with_tx()
        embedder = _FakeEmbedder()
        result = await build_chunks_for_memory(_FakePool(conn), embedder, _row(content))
        assert result == "built"
        assert len(embedder.calls) >= 2
        inserts = [c for c in conn.execute.call_args_list if "VALUES" in c.args[0]]
        assert len(inserts) == len(embedder.calls)
        # chunk_index sequential from 0
        assert [c.args[3] for c in inserts] == list(range(len(inserts)))

    @pytest.mark.asyncio
    async def test_embed_failure_records_backoff_and_writes_no_chunks(self) -> None:
        content = ("sentence about topic. " * 80 + "\n\n") * 4
        conn = _conn_with_tx()
        result = await build_chunks_for_memory(
            _FakePool(conn), _FakeEmbedder(fail=True), _row(content)
        )
        assert result == "failed_embed"
        executed = [c.args[0] for c in conn.execute.call_args_list]
        # Records backoff state (migration 0039) so the memory is NOT re-selected
        # every pass — a generic error is transient (not terminal).
        assert any("INSERT INTO chunk_build_state" in q for q in executed), executed
        # But no chunk rows are written on failure.
        assert not any("INSERT INTO memory_chunks" in q for q in executed)

    @pytest.mark.asyncio
    async def test_empty_content_skipped(self) -> None:
        conn = _conn_with_tx()
        result = await build_chunks_for_memory(_FakePool(conn), _FakeEmbedder(), _row("   "))
        assert result == "skipped_empty"

    @pytest.mark.asyncio
    async def test_fetch_candidates_query_shape(self) -> None:
        conn = AsyncMock()
        conn.fetch.return_value = []
        await fetch_candidates(conn, 25)
        query = conn.fetch.call_args[0][0]
        assert "indexable = true" in query
        assert "source_content_hash = m.content_hash" in query
        assert "deleted_at IS NULL" in query
        assert conn.fetch.call_args[0][1] == 25

    @pytest.mark.asyncio
    async def test_run_chunk_build_tally(self) -> None:
        conn = _conn_with_tx()
        conn.fetch.return_value = [_row("note one"), _row("note two")]
        tally = await run_chunk_build(_FakePool(conn), _FakeEmbedder(), batch_size=10)
        assert tally["processed"] == 2
        assert tally["built"] == 2
