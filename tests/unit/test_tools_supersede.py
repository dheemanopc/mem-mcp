"""Tests for memory.supersede tool (T-7.5)."""

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
from mem_mcp.mcp.tools.supersede import (
    MemorySupersedeInput,
    MemorySupersedeOutput,
    MemorySupersedeTool,
)


class _StubEmbeddings:
    """Stub embeddings client; supersede doesn't embed but ToolDeps requires one."""

    async def embed(self, text: str) -> EmbedResult:
        """Return a stub EmbedResult."""
        return EmbedResult(vector=[0.1] * 1024, input_tokens=len(text.split()))


def _patch_tenant_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    """Patch tenant_tx to yield our fake conn."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_tx(pool: Any, tenant_id: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.mcp.tools.supersede.tenant_tx", fake_tx)


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


class TestMemorySupersede:
    """Tests for memory.supersede tool."""

    @pytest.mark.asyncio
    async def test_supersede_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both decisions, both live → marks old superseded, new is_current=true, version bumped."""
        tool = MemorySupersedeTool()
        ctx = _build_ctx()
        old_id = uuid4()
        new_id = uuid4()
        inp = MemorySupersedeInput(old_id=old_id, new_id=new_id)

        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            # old_id lookup
            {
                "id": old_id,
                "type": "decision",
                "version": 1,
                "deleted_at": None,
            },
            # new_id lookup
            {
                "id": new_id,
                "type": "decision",
                "version": None,  # new memory, version will be set by supersede
                "deleted_at": None,
            },
        ]
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert isinstance(output, MemorySupersedeOutput)
        assert output.old_id == old_id
        assert output.new_id == new_id

    @pytest.mark.asyncio
    async def test_supersede_old_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Old id not found → -32602."""
        tool = MemorySupersedeTool()
        ctx = _build_ctx()
        inp = MemorySupersedeInput(old_id=uuid4(), new_id=uuid4())

        conn = AsyncMock()
        conn.fetchrow.return_value = None
        _patch_tenant_tx(monkeypatch, conn)

        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, inp)
        assert exc.value.code == -32602

    @pytest.mark.asyncio
    async def test_supersede_new_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """New id not found → -32602."""
        tool = MemorySupersedeTool()
        ctx = _build_ctx()
        old_id = uuid4()
        new_id = uuid4()
        inp = MemorySupersedeInput(old_id=old_id, new_id=new_id)

        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            # old_id lookup succeeds
            {
                "id": old_id,
                "type": "decision",
                "version": 1,
                "deleted_at": None,
            },
            # new_id lookup fails
            None,
        ]
        _patch_tenant_tx(monkeypatch, conn)

        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, inp)
        assert exc.value.code == -32602

    @pytest.mark.asyncio
    async def test_supersede_type_mismatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Old is decision, new is fact → -32602 type mismatch."""
        tool = MemorySupersedeTool()
        ctx = _build_ctx()
        old_id = uuid4()
        new_id = uuid4()
        inp = MemorySupersedeInput(old_id=old_id, new_id=new_id)

        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            # old_id lookup
            {
                "id": old_id,
                "type": "decision",
                "version": 1,
                "deleted_at": None,
            },
            # new_id lookup (different type)
            {
                "id": new_id,
                "type": "fact",
                "version": None,
                "deleted_at": None,
            },
        ]
        _patch_tenant_tx(monkeypatch, conn)

        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, inp)
        assert exc.value.code == -32602

    @pytest.mark.asyncio
    async def test_supersede_non_versioned_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both notes (non-versioned) → -32602 only versioned types allowed."""
        tool = MemorySupersedeTool()
        ctx = _build_ctx()
        old_id = uuid4()
        new_id = uuid4()
        inp = MemorySupersedeInput(old_id=old_id, new_id=new_id)

        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            # old_id lookup
            {
                "id": old_id,
                "type": "note",  # non-versioned
                "version": None,
                "deleted_at": None,
            },
            # new_id lookup (also non-versioned)
            {
                "id": new_id,
                "type": "note",  # non-versioned
                "version": None,
                "deleted_at": None,
            },
        ]
        _patch_tenant_tx(monkeypatch, conn)

        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, inp)
        assert exc.value.code == -32602

    @pytest.mark.asyncio
    async def test_supersede_either_deleted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Old or new has deleted_at set → -32602."""
        tool = MemorySupersedeTool()
        ctx = _build_ctx()
        old_id = uuid4()
        new_id = uuid4()
        inp = MemorySupersedeInput(old_id=old_id, new_id=new_id)

        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            # old_id lookup (deleted)
            {
                "id": old_id,
                "type": "decision",
                "version": 1,
                "deleted_at": datetime.now(tz=UTC),
            },
        ]
        _patch_tenant_tx(monkeypatch, conn)

        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, inp)
        assert exc.value.code == -32602

    @pytest.mark.asyncio
    async def test_supersede_new_deleted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """New has deleted_at set → -32602."""
        tool = MemorySupersedeTool()
        ctx = _build_ctx()
        old_id = uuid4()
        new_id = uuid4()
        inp = MemorySupersedeInput(old_id=old_id, new_id=new_id)

        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            # old_id lookup (live)
            {
                "id": old_id,
                "type": "decision",
                "version": 1,
                "deleted_at": None,
            },
            # new_id lookup (deleted)
            {
                "id": new_id,
                "type": "decision",
                "version": None,
                "deleted_at": datetime.now(tz=UTC),
            },
        ]
        _patch_tenant_tx(monkeypatch, conn)

        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, inp)
        assert exc.value.code == -32602
