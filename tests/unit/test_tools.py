"""Tests for the first three production tools (T-5.9, T-5.10, T-5.11)."""

from __future__ import annotations

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
from mem_mcp.mcp.tools.get import MemoryGetInput, MemoryGetOutput, MemoryGetTool
from mem_mcp.mcp.tools.search import MemorySearchInput, MemorySearchOutput, MemorySearchTool
from mem_mcp.mcp.tools.write import MemoryWriteInput, MemoryWriteOutput, MemoryWriteTool

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class FakeEmbeddings:
    def __init__(
        self,
        vector: list[float] | None = None,
        tokens: int = 12,
        error: Exception | None = None,
    ) -> None:
        self.vector = vector or [0.1] * 1024
        self.tokens = tokens
        self.error = error

    async def embed(self, text: str) -> EmbedResult:
        if self.error:
            raise self.error
        return EmbedResult(vector=self.vector, input_tokens=self.tokens)


def _build_ctx(
    *,
    embeddings: Any | None = None,
    db_pool: Any | None = None,
    scopes: tuple[str, ...] = ("memory.read", "memory.write"),
) -> ToolContext:
    deps = ToolDeps(
        embeddings=embeddings or FakeEmbeddings(),
        audit=NoopAuditLogger(),
        quotas=NoopQuotas(),
    )
    return ToolContext(
        request_id=str(uuid4()),
        tenant_id=uuid4(),
        identity_id=uuid4(),
        client_id="client-1",
        scopes=frozenset(scopes),
        db_pool=db_pool or MagicMock(),
        deps=deps,
    )


def _patch_tenant_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    """Patch tenant_tx to yield our fake conn (no real DB needed)."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_tx(pool: Any, tenant_id: Any) -> Any:
        yield conn

    # Patch in every module that imports tenant_tx
    monkeypatch.setattr("mem_mcp.mcp.tools.write.tenant_tx", fake_tx)
    monkeypatch.setattr("mem_mcp.mcp.tools.search.tenant_tx", fake_tx)
    monkeypatch.setattr("mem_mcp.mcp.tools.get.tenant_tx", fake_tx)


# --------------------------------------------------------------------------
# MemoryWriteInput validation
# --------------------------------------------------------------------------


class TestMemoryWriteInput:
    def test_minimal_valid(self) -> None:
        m = MemoryWriteInput.model_validate({"content": "hello"})
        assert m.type == "note"
        assert m.tags == []
        assert m.force_new is False

    def test_content_required(self) -> None:
        with pytest.raises(ValidationError):
            MemoryWriteInput.model_validate({})

    def test_content_max_len(self) -> None:
        with pytest.raises(ValidationError):
            MemoryWriteInput.model_validate({"content": "x" * 32_769})

    def test_tag_charset(self) -> None:
        # Valid
        MemoryWriteInput.model_validate({"content": "x", "tags": ["a-b", "p:q.r", "x_y"]})
        # Invalid (space)
        with pytest.raises(ValidationError):
            MemoryWriteInput.model_validate({"content": "x", "tags": ["bad tag"]})

    def test_duplicate_tags_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryWriteInput.model_validate({"content": "x", "tags": ["dup", "dup"]})

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryWriteInput.model_validate({"content": "x", "junk": True})


# --------------------------------------------------------------------------
# MemoryWriteTool
# --------------------------------------------------------------------------


class TestMemoryWriteTool:
    @pytest.mark.asyncio
    async def test_insert_when_no_dedupe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = AsyncMock()
        # check_dup queries: hash check returns None
        conn.fetchrow.side_effect = [
            None,  # hash check (in check_dup)
            None,  # cosine check (in check_dup)
            {
                "id": uuid4(),
                "version": 1,
                "created_at": datetime.now(tz=UTC),
            },  # final INSERT
        ]
        _patch_tenant_tx(monkeypatch, conn)

        tool = MemoryWriteTool()
        ctx = _build_ctx()
        result = await tool(ctx, MemoryWriteInput(content="brand new memory"))

        assert isinstance(result, MemoryWriteOutput)
        assert result.deduped is False
        assert result.merged_into is None

    @pytest.mark.asyncio
    async def test_dedupe_merge_on_hash_hit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        existing = uuid4()
        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            {"id": existing},  # hash check hits
            {
                "id": existing,
                "version": 3,
                "created_at": datetime.now(tz=UTC),
            },  # UPDATE returning
        ]
        _patch_tenant_tx(monkeypatch, conn)

        tool = MemoryWriteTool()
        ctx = _build_ctx()
        result = await tool(ctx, MemoryWriteInput(content="x", tags=["new-tag"]))
        assert isinstance(result, MemoryWriteOutput)
        assert result.deduped is True
        assert result.merged_into == existing
        assert result.id == existing

    @pytest.mark.asyncio
    async def test_force_new_skips_dedupe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            {
                "id": uuid4(),
                "version": 1,
                "created_at": datetime.now(tz=UTC),
            },
        ]
        _patch_tenant_tx(monkeypatch, conn)
        tool = MemoryWriteTool()
        ctx = _build_ctx()
        result = await tool(ctx, MemoryWriteInput(content="x", force_new=True))
        assert isinstance(result, MemoryWriteOutput)
        assert result.deduped is False

    @pytest.mark.asyncio
    async def test_embedding_failure_raises_jsonrpc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        embed = FakeEmbeddings(error=EmbeddingError("unavailable", "down", retry_after_seconds=4))
        ctx = _build_ctx(embeddings=embed)
        tool = MemoryWriteTool()
        with pytest.raises(JsonRpcError) as exc_info:
            await tool(ctx, MemoryWriteInput(content="x"))
        assert exc_info.value.code == -32000
        assert exc_info.value.data is not None
        assert exc_info.value.data["code"] == "embedding_unavailable"

    @pytest.mark.asyncio
    async def test_supersede_validates_target_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = AsyncMock()
        target = uuid4()
        # check_dup: no hash, no cosine. Then supersede target lookup returns wrong type.
        conn.fetchrow.side_effect = [
            None,
            None,
            {"id": target, "version": 1, "type": "note"},  # target is wrong type
        ]
        _patch_tenant_tx(monkeypatch, conn)
        tool = MemoryWriteTool()
        ctx = _build_ctx()
        with pytest.raises(JsonRpcError) as exc_info:
            await tool(
                ctx,
                MemoryWriteInput(
                    content="updated decision",
                    type="decision",
                    supersedes=target,
                ),
            )
        assert exc_info.value.code == -32602  # invalid params

    def test_supersede_only_versioned_types(self) -> None:
        # Verify that supersedes is only allowed for versioned types (decision/fact)
        with pytest.raises(ValidationError):
            MemoryWriteInput(content="x", type="note", supersedes=uuid4())


# --------------------------------------------------------------------------
# MemorySearchTool
# --------------------------------------------------------------------------


class TestMemorySearchTool:
    @pytest.mark.asyncio
    async def test_returns_results(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mem_mcp.memory.hybrid_query import SearchResult

        async def fake_hybrid_search(conn: Any, tenant_id: Any, params: Any) -> Any:
            return [
                SearchResult(
                    id=uuid4(),
                    content="hit",
                    type="note",
                    tags=["a"],
                    version=1,
                    created_at=datetime.now(tz=UTC),
                    updated_at=datetime.now(tz=UTC),
                    sem_score=0.9,
                    kw_score=0.5,
                    recency_factor=0.95,
                    score=0.8,
                )
            ]

        monkeypatch.setattr("mem_mcp.mcp.tools.search.hybrid_search", fake_hybrid_search)
        _patch_tenant_tx(monkeypatch, AsyncMock())

        tool = MemorySearchTool()
        ctx = _build_ctx()
        result = await tool(ctx, MemorySearchInput(query="hello"))
        assert isinstance(result, MemorySearchOutput)
        assert len(result.results) == 1
        assert result.results[0].score == 0.8
        assert result.results[0].scores_breakdown["semantic"] == 0.9
        assert result.query_embedding_tokens == 12

    @pytest.mark.asyncio
    async def test_embedding_failure_raises_jsonrpc(self) -> None:
        embed = FakeEmbeddings(error=EmbeddingError("throttled", "slow", retry_after_seconds=2))
        ctx = _build_ctx(embeddings=embed)
        tool = MemorySearchTool()
        with pytest.raises(JsonRpcError) as exc_info:
            await tool(ctx, MemorySearchInput(query="hello"))
        assert exc_info.value.code == -32000


class TestMemorySearchInput:
    def test_query_required(self) -> None:
        with pytest.raises(ValidationError):
            MemorySearchInput.model_validate({})

    def test_limit_range(self) -> None:
        MemorySearchInput.model_validate({"query": "x", "limit": 50})
        with pytest.raises(ValidationError):
            MemorySearchInput.model_validate({"query": "x", "limit": 0})
        with pytest.raises(ValidationError):
            MemorySearchInput.model_validate({"query": "x", "limit": 51})

    def test_default_limit(self) -> None:
        m = MemorySearchInput.model_validate({"query": "x"})
        assert m.limit == 10


# --------------------------------------------------------------------------
# MemoryGetTool
# --------------------------------------------------------------------------


class TestMemoryGetTool:
    @pytest.mark.asyncio
    async def test_returns_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mid = uuid4()
        row = {
            "id": mid,
            "content": "x",
            "type": "note",
            "tags": ["a"],
            "metadata": {},
            "version": 1,
            "is_current": True,
            "supersedes": None,
            "superseded_by": None,
            "created_at": datetime.now(tz=UTC),
            "updated_at": datetime.now(tz=UTC),
            "deleted_at": None,
        }
        conn = AsyncMock()
        conn.fetchrow.return_value = row
        _patch_tenant_tx(monkeypatch, conn)

        tool = MemoryGetTool()
        ctx = _build_ctx()
        result = await tool(ctx, MemoryGetInput(id=mid))
        assert isinstance(result, MemoryGetOutput)
        assert result.memory.id == mid
        assert result.history == []

    @pytest.mark.asyncio
    async def test_not_found_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = AsyncMock()
        conn.fetchrow.return_value = None
        _patch_tenant_tx(monkeypatch, conn)
        tool = MemoryGetTool()
        ctx = _build_ctx()
        with pytest.raises(JsonRpcError) as exc_info:
            await tool(ctx, MemoryGetInput(id=uuid4()))
        assert exc_info.value.code == -32602

    @pytest.mark.asyncio
    async def test_include_history_walks_chain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mid = uuid4()
        row = {
            "id": mid,
            "content": "v2",
            "type": "decision",
            "tags": [],
            "metadata": {},
            "version": 2,
            "is_current": True,
            "supersedes": uuid4(),
            "superseded_by": None,
            "created_at": datetime.now(tz=UTC),
            "updated_at": datetime.now(tz=UTC),
            "deleted_at": None,
        }
        conn = AsyncMock()
        conn.fetchrow.return_value = row
        # History query
        old_row = {
            **row,
            "version": 1,
            "id": uuid4(),
            "is_current": False,
            "content": "v1",
        }
        conn.fetch.return_value = [old_row]
        _patch_tenant_tx(monkeypatch, conn)

        tool = MemoryGetTool()
        result = await tool(_build_ctx(), MemoryGetInput(id=mid, include_history=True))
        assert isinstance(result, MemoryGetOutput)
        assert result.memory.version == 2
        assert len(result.history) == 1
        assert result.history[0].version == 1
