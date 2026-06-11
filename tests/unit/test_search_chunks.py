"""Tests for the memory_search_chunks tool."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mem_mcp.audit.logger import NoopAuditLogger
from mem_mcp.embeddings.bedrock import EmbeddingError, EmbedResult
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._deps import NoopQuotas, ToolDeps
from mem_mcp.mcp.tools.search_chunks import (
    _CHUNK_SEARCH_SQL,
    MemorySearchChunksInput,
    MemorySearchChunksOutput,
    MemorySearchChunksTool,
)


class _FakeEmbeddings:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def embed(self, text: str) -> EmbedResult:
        if self.error:
            raise self.error
        return EmbedResult(vector=[0.1] * 1024, input_tokens=7)


def _build_ctx(embeddings: Any | None = None) -> ToolContext:
    return ToolContext(
        request_id=str(uuid4()),
        tenant_id=uuid4(),
        identity_id=uuid4(),
        client_id="client-1",
        scopes=frozenset(("memory.read",)),
        db_pool=MagicMock(),
        deps=ToolDeps(
            embeddings=embeddings or _FakeEmbeddings(),
            audit=NoopAuditLogger(),
            quotas=NoopQuotas(),
        ),
    )


def _patch_tx(monkeypatch: pytest.MonkeyPatch, conn: Any) -> None:
    @asynccontextmanager
    async def fake_tx(pool: Any, tenant_id: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.mcp.tools.search_chunks.tenant_tx", fake_tx)


def _chunk_row(score: float = 0.8, chunk_index: int = 0) -> dict[str, Any]:
    return {
        "memory_id": uuid4(),
        "chunk_index": chunk_index,
        "chunk_content": "the retry budget is three attempts with exponential backoff",
        "score": score,
        "type": "decision",
        "tags": ["retries"],
        "created_at": datetime.now(tz=UTC),
        "total_chunks": 7,
    }


class TestInputValidation:
    def test_query_required(self) -> None:
        with pytest.raises(ValidationError):
            MemorySearchChunksInput.model_validate({})

    def test_defaults(self) -> None:
        m = MemorySearchChunksInput.model_validate({"query": "x"})
        assert m.limit == 10
        assert m.per_memory == 2
        assert m.type is None

    @pytest.mark.parametrize(
        "field,bad", [("limit", 0), ("limit", 51), ("per_memory", 0), ("per_memory", 6)]
    )
    def test_bounds(self, field: str, bad: int) -> None:
        with pytest.raises(ValidationError):
            MemorySearchChunksInput.model_validate({"query": "x", field: bad})

    def test_extra_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemorySearchChunksInput.model_validate({"query": "x", "junk": 1})


class TestChunkSearchSql:
    def test_filters_present(self) -> None:
        assert "m.deleted_at IS NULL" in _CHUNK_SEARCH_SQL
        assert "m.is_current = true" in _CHUNK_SEARCH_SQL
        assert "m.indexable = true" in _CHUNK_SEARCH_SQL
        assert "c.embedding IS NOT NULL" in _CHUNK_SEARCH_SQL

    def test_per_memory_window(self) -> None:
        assert "ROW_NUMBER() OVER" in _CHUNK_SEARCH_SQL
        assert "PARTITION BY c.memory_id" in _CHUNK_SEARCH_SQL


class TestMemorySearchChunksTool:
    @pytest.mark.asyncio
    async def test_maps_rows_to_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = _chunk_row()
        conn = AsyncMock()
        conn.fetch.return_value = [row]
        _patch_tx(monkeypatch, conn)
        monkeypatch.setattr(
            "mem_mcp.mcp.tools.search_chunks.bump_access", AsyncMock(return_value=None)
        )

        out = await MemorySearchChunksTool()(
            _build_ctx(), MemorySearchChunksInput(query="retry budget")
        )
        assert isinstance(out, MemorySearchChunksOutput)
        item = out.results[0]
        assert item.memory_id == row["memory_id"]
        assert item.snippet == row["chunk_content"]
        assert item.chunk_index == 0
        assert item.total_chunks == 7
        assert item.score == 0.8
        assert out.query_embedding_tokens == 7

    @pytest.mark.asyncio
    async def test_params_passed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = AsyncMock()
        conn.fetch.return_value = []
        _patch_tx(monkeypatch, conn)
        monkeypatch.setattr(
            "mem_mcp.mcp.tools.search_chunks.bump_access", AsyncMock(return_value=None)
        )

        ctx = _build_ctx()
        await MemorySearchChunksTool()(
            ctx,
            MemorySearchChunksInput(query="x", type="decision", tags=["a"], limit=5, per_memory=1),
        )
        args = conn.fetch.call_args[0]
        # (sql, qvec, tenant, type, tags, pool, per_memory, limit)
        assert args[2] == ctx.tenant_id
        assert args[3] == "decision"
        assert args[4] == ["a"]
        assert args[6] == 1
        assert args[7] == 5

    @pytest.mark.asyncio
    async def test_embedding_failure_raises_jsonrpc(self) -> None:
        embed = _FakeEmbeddings(error=EmbeddingError("throttled", "slow", retry_after_seconds=2))
        with pytest.raises(JsonRpcError) as exc_info:
            await MemorySearchChunksTool()(
                _build_ctx(embeddings=embed), MemorySearchChunksInput(query="x")
            )
        assert exc_info.value.code == -32000
