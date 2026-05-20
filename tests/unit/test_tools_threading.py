"""Tests for native-threading feature (parent_id on writes/searches + memory_thread_get + reply cascade on delete/undelete)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mem_mcp.audit.logger import NoopAuditLogger
from mem_mcp.embeddings.bedrock import EmbedResult
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._deps import NoopQuotas, ToolDeps
from mem_mcp.mcp.tools.delete import MemoryDeleteInput, MemoryDeleteOutput, MemoryDeleteTool
from mem_mcp.mcp.tools.search import MemorySearchInput
from mem_mcp.mcp.tools.thread_get import (
    MemoryThreadGetInput,
    MemoryThreadGetOutput,
    MemoryThreadGetTool,
)
from mem_mcp.mcp.tools.undelete import MemoryUndeleteInput, MemoryUndeleteOutput, MemoryUndeleteTool
from mem_mcp.mcp.tools.write import MemoryWriteInput, MemoryWriteOutput, MemoryWriteTool


class _FakeEmbeddings:
    async def embed(self, text: str) -> EmbedResult:
        return EmbedResult(vector=[0.1] * 1024, input_tokens=10)


def _build_ctx(scopes: tuple[str, ...] = ("memory.read", "memory.write")) -> ToolContext:
    deps = ToolDeps(
        embeddings=_FakeEmbeddings(),
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


def _patch_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_tx(pool: Any, tenant_id: Any) -> Any:
        yield conn

    for mod in (
        "mem_mcp.mcp.tools.write",
        "mem_mcp.mcp.tools.thread_get",
        "mem_mcp.mcp.tools.delete",
        "mem_mcp.mcp.tools.undelete",
    ):
        monkeypatch.setattr(f"{mod}.tenant_tx", fake_tx)


class TestMemoryWriteInputThreading:
    def test_parent_id_accepts_uuid(self) -> None:
        pid = uuid4()
        m = MemoryWriteInput.model_validate({"content": "x", "parent_id": str(pid)})
        assert m.parent_id == pid

    def test_parent_id_default_none(self) -> None:
        m = MemoryWriteInput.model_validate({"content": "x"})
        assert m.parent_id is None

    def test_parent_id_with_supersedes_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            MemoryWriteInput.model_validate(
                {
                    "content": "x",
                    "type": "decision",
                    "supersedes": str(uuid4()),
                    "parent_id": str(uuid4()),
                }
            )
        assert "cannot be combined" in str(exc.value)


class TestMemoryWriteToolReply:
    @pytest.mark.asyncio
    async def test_reply_write_inherits_parent_tags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        parent_id = uuid4()
        reply_id = uuid4()
        conn = AsyncMock()
        # 1st fetchrow: parent SELECT
        # 2nd fetchrow: reply INSERT RETURNING
        conn.fetchrow.side_effect = [
            {
                "id": parent_id,
                "tags": ["alpha", "beta"],
                "parent_id": None,
                "deleted_at": None,
                "embedding_status": "ok",
            },
            {"id": reply_id, "version": 1, "created_at": datetime.now(tz=UTC)},
        ]
        _patch_tx(monkeypatch, conn)

        tool = MemoryWriteTool()
        ctx = _build_ctx()
        out = await tool(
            ctx,
            MemoryWriteInput(content="this is a reply", tags=["gamma"], parent_id=parent_id),
        )

        assert isinstance(out, MemoryWriteOutput)
        assert out.deduped is False
        # The INSERT bound effective_tags as the 7th positional arg of the call.
        insert_call = conn.fetchrow.call_args_list[1]
        bound_tags = insert_call.args[
            7
        ]  # effective_tags positional in INSERT (8th value after sql arg = index 7)
        assert sorted(bound_tags) == ["alpha", "beta", "gamma"]

    @pytest.mark.asyncio
    async def test_reply_to_missing_parent_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = AsyncMock()
        conn.fetchrow.side_effect = [None]  # parent SELECT returns nothing
        _patch_tx(monkeypatch, conn)

        tool = MemoryWriteTool()
        ctx = _build_ctx()
        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, MemoryWriteInput(content="x", parent_id=uuid4()))
        assert exc.value.code == -32602
        assert "parent_id not found" in exc.value.message

    @pytest.mark.asyncio
    async def test_reply_to_deleted_parent_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            {
                "id": uuid4(),
                "tags": [],
                "parent_id": None,
                "deleted_at": datetime.now(tz=UTC),
            }
        ]
        _patch_tx(monkeypatch, conn)

        tool = MemoryWriteTool()
        ctx = _build_ctx()
        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, MemoryWriteInput(content="x", parent_id=uuid4()))
        assert exc.value.code == -32602
        assert "soft-deleted" in exc.value.message

    @pytest.mark.asyncio
    async def test_reply_to_reply_rejected_flat_hierarchy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            {
                "id": uuid4(),
                "tags": [],
                "parent_id": uuid4(),  # parent IS itself a reply
                "deleted_at": None,
                "embedding_status": "ok",
            }
        ]
        _patch_tx(monkeypatch, conn)

        tool = MemoryWriteTool()
        ctx = _build_ctx()
        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, MemoryWriteInput(content="x", parent_id=uuid4()))
        assert exc.value.code == -32602
        assert "flat hierarchy" in exc.value.message

    @pytest.mark.asyncio
    async def test_reply_tag_overflow_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # parent has 30 tags, reply adds 5 → 35 total > 32 cap
        conn = AsyncMock()
        parent_tags = [f"tag-{i}" for i in range(30)]
        conn.fetchrow.side_effect = [
            {"id": uuid4(), "tags": parent_tags, "parent_id": None, "deleted_at": None}
        ]
        _patch_tx(monkeypatch, conn)

        tool = MemoryWriteTool()
        ctx = _build_ctx()
        # Note: input.tags must individually be <= 32 entries (validator), so add 5 new ones.
        with pytest.raises(JsonRpcError) as exc:
            await tool(
                ctx,
                MemoryWriteInput(
                    content="x",
                    tags=["new-a", "new-b", "new-c", "new-d", "new-e"],
                    parent_id=uuid4(),
                ),
            )
        assert exc.value.code == -32602
        assert "tag count exceeds 32" in exc.value.message


class TestMemorySearchInputThreading:
    def test_parent_id_accepts_uuid(self) -> None:
        pid = uuid4()
        m = MemorySearchInput.model_validate({"query": "x", "parent_id": str(pid)})
        assert m.parent_id == pid

    def test_parent_id_default_none(self) -> None:
        m = MemorySearchInput.model_validate({"query": "x"})
        assert m.parent_id is None


class TestMemoryThreadGetTool:
    @pytest.mark.asyncio
    async def test_returns_root_and_replies_sorted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        root_id = uuid4()
        r1, r2 = uuid4(), uuid4()
        now = datetime.now(tz=UTC)
        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            {
                "id": root_id,
                "content": "root",
                "type": "decision",
                "tags": ["x"],
                "metadata": {},
                "version": 1,
                "is_current": True,
                "parent_id": None,
                "supersedes": None,
                "superseded_by": None,
                "created_at": now,
                "updated_at": now,
                "deleted_at": None,
                "embedding_status": "ok",
            }
        ]
        conn.fetch.return_value = [
            {
                "id": r1,
                "content": "first reply",
                "type": "note",
                "tags": ["x"],
                "metadata": {},
                "version": 1,
                "is_current": True,
                "parent_id": root_id,
                "supersedes": None,
                "superseded_by": None,
                "created_at": now,
                "updated_at": now,
                "deleted_at": None,
                "embedding_status": "ok",
            },
            {
                "id": r2,
                "content": "second reply",
                "type": "note",
                "tags": ["x"],
                "metadata": {},
                "version": 1,
                "is_current": True,
                "parent_id": root_id,
                "supersedes": None,
                "superseded_by": None,
                "created_at": now,
                "updated_at": now,
                "deleted_at": None,
                "embedding_status": "ok",
            },
        ]
        _patch_tx(monkeypatch, conn)

        tool = MemoryThreadGetTool()
        ctx = _build_ctx()
        out = await tool(ctx, MemoryThreadGetInput(root_id=root_id))

        assert isinstance(out, MemoryThreadGetOutput)
        assert out.root.id == root_id
        assert [r.id for r in out.replies] == [r1, r2]

    @pytest.mark.asyncio
    async def test_root_not_found_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = AsyncMock()
        conn.fetchrow.side_effect = [None]
        _patch_tx(monkeypatch, conn)

        tool = MemoryThreadGetTool()
        ctx = _build_ctx()
        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, MemoryThreadGetInput(root_id=uuid4()))
        assert exc.value.code == -32602
        assert "not found" in exc.value.message

    @pytest.mark.asyncio
    async def test_root_soft_deleted_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            {
                "id": uuid4(),
                "content": "x",
                "type": "note",
                "tags": [],
                "metadata": {},
                "version": 1,
                "is_current": False,
                "parent_id": None,
                "supersedes": None,
                "superseded_by": None,
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
                "deleted_at": datetime.now(tz=UTC),
            }
        ]
        _patch_tx(monkeypatch, conn)

        tool = MemoryThreadGetTool()
        ctx = _build_ctx()
        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, MemoryThreadGetInput(root_id=uuid4()))
        assert exc.value.code == -32602
        assert "soft-deleted" in exc.value.message

    @pytest.mark.asyncio
    async def test_root_id_is_a_reply_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            {
                "id": uuid4(),
                "content": "x",
                "type": "note",
                "tags": [],
                "metadata": {},
                "version": 1,
                "is_current": True,
                "parent_id": uuid4(),  # IS a reply
                "supersedes": None,
                "superseded_by": None,
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
                "deleted_at": None,
                "embedding_status": "ok",
            }
        ]
        _patch_tx(monkeypatch, conn)

        tool = MemoryThreadGetTool()
        ctx = _build_ctx()
        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, MemoryThreadGetInput(root_id=uuid4()))
        assert exc.value.code == -32602
        assert "refers to a reply" in exc.value.message

    @pytest.mark.asyncio
    async def test_empty_thread_returns_empty_replies(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = datetime.now(tz=UTC)
        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            {
                "id": uuid4(),
                "content": "lonely root",
                "type": "decision",
                "tags": [],
                "metadata": {},
                "version": 1,
                "is_current": True,
                "parent_id": None,
                "supersedes": None,
                "superseded_by": None,
                "created_at": now,
                "updated_at": now,
                "deleted_at": None,
                "embedding_status": "ok",
            }
        ]
        conn.fetch.return_value = []
        _patch_tx(monkeypatch, conn)

        tool = MemoryThreadGetTool()
        ctx = _build_ctx()
        out = await tool(ctx, MemoryThreadGetInput(root_id=uuid4()))
        assert isinstance(out, MemoryThreadGetOutput)
        assert out.replies == []


class TestDeleteReplyCascade:
    @pytest.mark.asyncio
    async def test_delete_root_cascades_to_replies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        root_id = uuid4()
        del_at = datetime.now(tz=UTC)
        conn = AsyncMock()
        # 1. lookup target → root
        # 2. UPDATE the row → deleted_at
        # 3. cascade UPDATE replies (fetch) → [r1, r2]
        conn.fetchrow.side_effect = [
            {
                "id": root_id,
                "type": "note",
                "supersedes": None,
                "is_current": True,
                "deleted_at": None,
                "embedding_status": "ok",
                "parent_id": None,
            }
        ]
        conn.fetchval.return_value = del_at
        conn.fetch.return_value = [{"id": uuid4()}, {"id": uuid4()}]
        _patch_tx(monkeypatch, conn)

        tool = MemoryDeleteTool()
        ctx = _build_ctx()
        out = await tool(ctx, MemoryDeleteInput(id=root_id, cascade=False))
        assert isinstance(out, MemoryDeleteOutput)
        assert out.replies_cascaded_count == 2

    @pytest.mark.asyncio
    async def test_delete_reply_does_not_cascade(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reply_id = uuid4()
        parent_id = uuid4()
        del_at = datetime.now(tz=UTC)
        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            {
                "id": reply_id,
                "type": "note",
                "supersedes": None,
                "is_current": True,
                "deleted_at": None,
                "embedding_status": "ok",
                "parent_id": parent_id,  # target is a reply
            }
        ]
        conn.fetchval.return_value = del_at
        _patch_tx(monkeypatch, conn)

        tool = MemoryDeleteTool()
        ctx = _build_ctx()
        out = await tool(ctx, MemoryDeleteInput(id=reply_id, cascade=False))
        assert isinstance(out, MemoryDeleteOutput)
        assert out.replies_cascaded_count == 0
        # conn.fetch was NOT called (no cascade UPDATE)
        conn.fetch.assert_not_called()


class TestUndeleteReplyCascadeReversal:
    @pytest.mark.asyncio
    async def test_undelete_root_restores_replies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from datetime import timedelta

        root_id = uuid4()
        del_at = datetime.now(tz=UTC) - timedelta(hours=1)
        conn = AsyncMock()
        # 1. lookup target (with age)
        # 2. main UPDATE → restored row
        # 3. cascade reversal fetch
        conn.fetchrow.side_effect = [
            {
                "id": root_id,
                "type": "note",
                "deleted_at": del_at,
                "supersedes": None,
                "superseded_by": None,
                "is_current": False,
                "parent_id": None,
                "age": timedelta(hours=1),
            },
            {"deleted_at": None, "is_current": True},
        ]
        conn.fetch.return_value = [{"id": uuid4()}, {"id": uuid4()}]
        _patch_tx(monkeypatch, conn)

        tool = MemoryUndeleteTool()
        ctx = _build_ctx()
        out = await tool(ctx, MemoryUndeleteInput(id=root_id))
        assert isinstance(out, MemoryUndeleteOutput)
        assert out.replies_restored_count == 2

    @pytest.mark.asyncio
    async def test_undelete_reply_does_not_reverse_cascade(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import timedelta

        reply_id = uuid4()
        parent_id = uuid4()
        del_at = datetime.now(tz=UTC) - timedelta(hours=1)
        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            {
                "id": reply_id,
                "type": "note",
                "deleted_at": del_at,
                "supersedes": None,
                "superseded_by": None,
                "is_current": False,
                "parent_id": parent_id,
                "age": timedelta(hours=1),
            },
            {"deleted_at": None, "is_current": True},
        ]
        _patch_tx(monkeypatch, conn)

        tool = MemoryUndeleteTool()
        ctx = _build_ctx()
        out = await tool(ctx, MemoryUndeleteInput(id=reply_id))
        assert isinstance(out, MemoryUndeleteOutput)
        assert out.replies_restored_count == 0
        conn.fetch.assert_not_called()
