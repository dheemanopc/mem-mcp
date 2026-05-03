"""Tests for memory.update tool (T-7.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mem_mcp.audit.logger import NoopAuditLogger
from mem_mcp.embeddings.bedrock import EmbedResult
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._deps import NoopQuotas, ToolDeps
from mem_mcp.mcp.tools.update import (
    MemoryUpdateInput,
    MemoryUpdateOutput,
    MemoryUpdateTool,
)


class _StubEmbeddings:
    """Stub embeddings client that returns a valid EmbedResult."""

    async def embed(self, text: str) -> EmbedResult:
        """Return a stub EmbedResult with a fake vector."""
        return EmbedResult(vector=[0.1] * 1024, input_tokens=len(text.split()))


def _patch_tenant_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    """Patch tenant_tx to yield our fake conn."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_tx(pool: Any, tenant_id: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.mcp.tools.update.tenant_tx", fake_tx)


def _build_ctx(scopes: tuple[str, ...] = ("memory.write",)) -> ToolContext:
    """Build a ToolContext with proper dependencies."""
    deps = ToolDeps(
        embeddings=_StubEmbeddings(),
        audit=NoopAuditLogger(),
        quotas=NoopQuotas(),
    )
    return ToolContext(
        request_id=str(uuid4()),
        tenant_id=uuid4(),
        identity_id=uuid4(),
        client_id="client-1",
        scopes=frozenset(scopes),
        db_pool=MagicMock(),
        deps=deps,
    )


class TestMemoryUpdate:
    """Tests for memory.update tool."""

    @pytest.mark.asyncio
    async def test_update_in_place_note_content_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Note type with content change → in-place UPDATE, is_new_version=False."""
        tool = MemoryUpdateTool()
        ctx = _build_ctx()
        target_id = uuid4()
        inp = MemoryUpdateInput(id=target_id, content="new content")

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": target_id,
            "type": "note",  # non-versioned
            "content": "old content",
            "tags": [],
            "metadata": {},
            "deleted_at": None,
        }
        conn.fetchval.return_value = datetime.now(tz=UTC)
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert isinstance(output, MemoryUpdateOutput)
        assert output.id == target_id
        assert output.is_new_version is False

    @pytest.mark.asyncio
    async def test_update_tags_only_no_re_embed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only tags changing on a decision → in-place, no embedding call, is_new_version=False."""
        tool = MemoryUpdateTool()
        ctx = _build_ctx()
        target_id = uuid4()
        inp = MemoryUpdateInput(id=target_id, tags=["new-tag"])

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": target_id,
            "type": "decision",  # versioned, but no content change
            "content": "existing content",
            "tags": ["old-tag"],
            "metadata": {},
            "deleted_at": None,
        }
        conn.fetchval.return_value = datetime.now(tz=UTC)
        _patch_tenant_tx(monkeypatch, conn)

        # Spy on embeddings to ensure it's NOT called
        embeddings_spy = MagicMock(wraps=ctx.deps.embeddings)
        ctx.deps.embeddings = embeddings_spy

        output = await tool(ctx, inp)
        assert isinstance(output, MemoryUpdateOutput)
        assert output.is_new_version is False
        embeddings_spy.embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_decision_content_change_creates_version(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Decision + content change → new version, is_new_version=True."""
        tool = MemoryUpdateTool()
        ctx = _build_ctx()
        target_id = uuid4()
        new_version_id = uuid4()
        inp = MemoryUpdateInput(id=target_id, content="new content")

        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            # Initial lookup
            {
                "id": target_id,
                "type": "decision",  # versioned
                "content": "old content",
                "version": 1,
                "tags": [],
                "metadata": {},
                "deleted_at": None,
            },
            # INSERT result
            {
                "id": new_version_id,
                "version": 2,
            },
        ]
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert isinstance(output, MemoryUpdateOutput)
        assert output.is_new_version is True
        assert output.version == 2

    @pytest.mark.asyncio
    async def test_update_fact_content_change_creates_version(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fact + content change → new version, is_new_version=True."""
        tool = MemoryUpdateTool()
        ctx = _build_ctx()
        target_id = uuid4()
        new_version_id = uuid4()
        inp = MemoryUpdateInput(id=target_id, content="new fact content")

        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            # Initial lookup
            {
                "id": target_id,
                "type": "fact",  # versioned
                "content": "old fact",
                "version": 1,
                "tags": [],
                "metadata": {},
                "deleted_at": None,
            },
            # INSERT result
            {
                "id": new_version_id,
                "version": 2,
            },
        ]
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert isinstance(output, MemoryUpdateOutput)
        assert output.is_new_version is True

    @pytest.mark.asyncio
    async def test_update_type_promotion_note_to_decision(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Note → decision (non-versioned to versioned) → new version, is_new_version=True."""
        tool = MemoryUpdateTool()
        ctx = _build_ctx()
        target_id = uuid4()
        new_version_id = uuid4()
        inp = MemoryUpdateInput(id=target_id, type="decision")

        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            # Initial lookup
            {
                "id": target_id,
                "type": "note",  # non-versioned → will change to versioned
                "content": "content",
                "version": None,
                "tags": [],
                "metadata": {},
                "deleted_at": None,
            },
            # INSERT result for new versioned chain (version=1)
            {
                "id": new_version_id,
                "version": 1,
            },
        ]
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert isinstance(output, MemoryUpdateOutput)
        assert output.is_new_version is True

    @pytest.mark.asyncio
    async def test_update_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Memory not found → -32602."""
        tool = MemoryUpdateTool()
        ctx = _build_ctx()
        inp = MemoryUpdateInput(id=uuid4(), content="new")

        conn = AsyncMock()
        conn.fetchrow.return_value = None
        _patch_tenant_tx(monkeypatch, conn)

        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, inp)
        assert exc.value.code == -32602
        assert "not found" in exc.value.message.lower()

    @pytest.mark.asyncio
    async def test_update_deleted_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cannot update deleted memory → -32602."""
        tool = MemoryUpdateTool()
        ctx = _build_ctx()
        target_id = uuid4()
        inp = MemoryUpdateInput(id=target_id, content="new")

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": target_id,
            "type": "note",
            "deleted_at": datetime.now(tz=UTC),
            "content": "old",
            "tags": [],
            "metadata": {},
        }
        _patch_tenant_tx(monkeypatch, conn)

        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, inp)
        assert exc.value.code == -32602
        assert "cannot update deleted" in exc.value.message.lower()

    @pytest.mark.asyncio
    async def test_update_tags_op_add(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """tags_op='add' with existing ['a','b'] and new ['b','c'] → ['a','b','c']."""
        tool = MemoryUpdateTool()
        ctx = _build_ctx()
        target_id = uuid4()
        inp = MemoryUpdateInput(id=target_id, tags=["b", "c"], tags_op="add")

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": target_id,
            "type": "note",
            "content": "content",
            "tags": ["a", "b"],
            "metadata": {},
            "deleted_at": None,
        }
        conn.fetchval.return_value = datetime.now(tz=UTC)
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert isinstance(output, MemoryUpdateOutput)
        assert set(output.tags or []) == {"a", "b", "c"}

    @pytest.mark.asyncio
    async def test_update_tags_op_remove(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """tags_op='remove' with existing ['a','b','c'] and remove ['b'] → ['a','c']."""
        tool = MemoryUpdateTool()
        ctx = _build_ctx()
        target_id = uuid4()
        inp = MemoryUpdateInput(id=target_id, tags=["b"], tags_op="remove")

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": target_id,
            "type": "note",
            "content": "content",
            "tags": ["a", "b", "c"],
            "metadata": {},
            "deleted_at": None,
        }
        conn.fetchval.return_value = datetime.now(tz=UTC)
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert isinstance(output, MemoryUpdateOutput)
        assert set(output.tags or []) == {"a", "c"}

    @pytest.mark.asyncio
    async def test_update_no_changes_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No changes (only id, no content/type/tags/metadata) → still succeeds, in-place, is_new_version=False."""
        tool = MemoryUpdateTool()
        ctx = _build_ctx()
        target_id = uuid4()
        # Only id, no other fields
        inp = MemoryUpdateInput(id=target_id)

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": target_id,
            "type": "note",
            "content": "content",
            "tags": [],
            "metadata": {},
            "deleted_at": None,
        }
        conn.fetchval.return_value = datetime.now(tz=UTC)
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert isinstance(output, MemoryUpdateOutput)
        # Per spec, no-op still returns success, is_new_version=False
        assert output.is_new_version is False
