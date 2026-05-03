"""Tests for memory.export tool (T-7.6)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mem_mcp.audit.logger import NoopAuditLogger
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._deps import NoopQuotas, ToolDeps
from mem_mcp.mcp.tools.export import (
    MemoryExportInput,
    MemoryExportOutput,
    MemoryExportTool,
)


class _StubEmbeddings:
    """Stub embeddings client; export doesn't embed but ToolDeps requires one."""

    async def embed(self, text: str) -> None:
        raise RuntimeError("export should never embed")


def _patch_tenant_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    """Patch tenant_tx to yield our fake conn."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_tx(pool: Any, tenant_id: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.mcp.tools.export.tenant_tx", fake_tx)


def _build_ctx(scopes: tuple[str, ...] = ("memory.admin",)) -> ToolContext:
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


class TestMemoryExport:
    """Tests for memory.export tool."""

    @pytest.mark.asyncio
    async def test_export_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Admin scope, both fetches return rows, output has counts and timestamp."""
        tool = MemoryExportTool()
        ctx = _build_ctx()
        inp = MemoryExportInput()

        memory_rows = [
            {
                "id": uuid4(),
                "type": "note",
                "content": "test memory",
                "tags": ["tag1"],
                "metadata": {},
                "version": None,
                "supersedes": None,
                "superseded_by": None,
                "is_current": True,
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
                "deleted_at": None,
            },
        ]
        audit_rows = [
            {
                "id": 1,
                "action": "memory.write",
                "result": "success",
                "target_id": memory_rows[0]["id"],
                "target_kind": "memory",
                "request_id": ctx.request_id,
                "details": {},
                "created_at": datetime.now(tz=UTC),
            },
        ]

        conn = AsyncMock()
        conn.fetch.side_effect = [memory_rows, audit_rows]
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert isinstance(output, MemoryExportOutput)
        assert len(output.memories) == 1
        assert len(output.audit_log) == 1
        assert isinstance(output.exported_at, datetime)
        assert output.request_id == ctx.request_id

    @pytest.mark.asyncio
    async def test_export_without_memory_admin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-admin scope raises -32000 with insufficient_scope."""
        tool = MemoryExportTool()
        ctx = _build_ctx(scopes=("memory.read", "memory.write"))
        inp = MemoryExportInput()
        conn = AsyncMock()
        _patch_tenant_tx(monkeypatch, conn)

        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, inp)
        assert exc.value.code == -32000
        assert "memory.admin" in exc.value.message.lower()
        assert exc.value.data is not None
        assert exc.value.data.get("code") == "insufficient_scope"
        assert exc.value.data.get("required_scopes") == ["memory.admin"]

    @pytest.mark.asyncio
    async def test_export_empty_tenant(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty tenant returns empty lists."""
        tool = MemoryExportTool()
        ctx = _build_ctx()
        inp = MemoryExportInput()

        conn = AsyncMock()
        conn.fetch.side_effect = [[], []]
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert isinstance(output, MemoryExportOutput)
        assert len(output.memories) == 0
        assert len(output.audit_log) == 0
        assert isinstance(output.exported_at, datetime)

    @pytest.mark.asyncio
    async def test_export_includes_deleted_and_superseded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Export includes soft-deleted and superseded memories (full dump)."""
        tool = MemoryExportTool()
        ctx = _build_ctx()
        inp = MemoryExportInput()

        id_current = uuid4()
        id_prior = uuid4()
        id_deleted = uuid4()

        memory_rows = [
            {
                "id": id_prior,
                "type": "decision",
                "content": "prior version",
                "tags": [],
                "metadata": {},
                "version": 1,
                "supersedes": None,
                "superseded_by": id_current,
                "is_current": False,
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
                "deleted_at": None,
            },
            {
                "id": id_current,
                "type": "decision",
                "content": "current version",
                "tags": [],
                "metadata": {},
                "version": 2,
                "supersedes": id_prior,
                "superseded_by": None,
                "is_current": True,
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
                "deleted_at": None,
            },
            {
                "id": id_deleted,
                "type": "note",
                "content": "deleted memory",
                "tags": [],
                "metadata": {},
                "version": None,
                "supersedes": None,
                "superseded_by": None,
                "is_current": False,
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
                "deleted_at": datetime.now(tz=UTC),
            },
        ]

        conn = AsyncMock()
        conn.fetch.side_effect = [memory_rows, []]
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert len(output.memories) == 3
        # Verify deleted and non-current are included
        deleted_in_output = [m for m in output.memories if m["id"] == id_deleted]
        assert len(deleted_in_output) == 1
        assert deleted_in_output[0]["deleted_at"] is not None
        superseded_in_output = [m for m in output.memories if m["id"] == id_prior]
        assert len(superseded_in_output) == 1
        assert superseded_in_output[0]["is_current"] is False
