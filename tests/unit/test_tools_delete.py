"""Tests for memory.delete tool (T-7.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mem_mcp.audit.logger import NoopAuditLogger
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._deps import NoopQuotas, ToolDeps
from mem_mcp.mcp.tools.delete import MemoryDeleteInput, MemoryDeleteOutput, MemoryDeleteTool


def _patch_tenant_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    """Patch tenant_tx to yield our fake conn."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_tx(pool: Any, tenant_id: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.mcp.tools.delete.tenant_tx", fake_tx)


def _build_ctx(scopes: tuple[str, ...] = ("memory.write",)) -> ToolContext:
    """Build a ToolContext with proper dependencies."""
    deps = ToolDeps(
        embeddings=None,  # Not used in delete
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


class TestMemoryDelete:
    """Tests for memory.delete tool."""

    @pytest.mark.asyncio
    async def test_delete_current_version_non_versioned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Plain delete on non-versioned type sets deleted_at."""
        tool = MemoryDeleteTool()
        ctx = _build_ctx()
        inp = MemoryDeleteInput(id=uuid4(), cascade=False)

        target_id = inp.id
        conn = AsyncMock()
        now = datetime.now(tz=UTC)
        conn.fetchrow.return_value = {
            "id": target_id,
            "type": "note",  # non-versioned
            "supersedes": None,
            "is_current": True,
            "deleted_at": None,
        }
        conn.fetchval.return_value = now
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert isinstance(output, MemoryDeleteOutput)
        assert output.id == target_id
        assert output.deleted_at == now
        assert output.promoted_version_id is None
        assert output.cascaded_count == 0

    @pytest.mark.asyncio
    async def test_delete_already_deleted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Delete on already-deleted memory raises -32602."""
        tool = MemoryDeleteTool()
        ctx = _build_ctx()
        inp = MemoryDeleteInput(id=uuid4(), cascade=False)

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": inp.id,
            "type": "note",
            "supersedes": None,
            "is_current": False,
            "deleted_at": datetime.now(tz=UTC),
        }
        _patch_tenant_tx(monkeypatch, conn)

        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, inp)
        assert exc.value.code == -32602
        assert "already deleted" in exc.value.message.lower()

    @pytest.mark.asyncio
    async def test_delete_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Delete on non-existent memory raises -32602."""
        tool = MemoryDeleteTool()
        ctx = _build_ctx()
        inp = MemoryDeleteInput(id=uuid4(), cascade=False)

        conn = AsyncMock()
        conn.fetchrow.return_value = None
        _patch_tenant_tx(monkeypatch, conn)

        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, inp)
        assert exc.value.code == -32602
        assert "not found" in exc.value.message.lower()

    @pytest.mark.asyncio
    async def test_cascade_without_memory_admin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """cascade=true without memory.admin scope raises -32000."""
        tool = MemoryDeleteTool()
        ctx = _build_ctx(scopes=("memory.write",))  # no memory.admin
        inp = MemoryDeleteInput(id=uuid4(), cascade=True)
        conn = AsyncMock()
        _patch_tenant_tx(monkeypatch, conn)

        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, inp)
        assert exc.value.code == -32000
        assert "cascade" in exc.value.message.lower()
        assert exc.value.data is not None
        assert exc.value.data.get("code") == "insufficient_scope"

    @pytest.mark.asyncio
    async def test_cascade_with_memory_admin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """cascade=true with memory.admin scope deletes entire chain."""
        tool = MemoryDeleteTool()
        ctx = _build_ctx(scopes=("memory.write", "memory.admin"))
        target_id = uuid4()
        inp = MemoryDeleteInput(id=target_id, cascade=True)

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": target_id,
            "type": "decision",  # versioned
            "supersedes": None,
            "is_current": True,
            "deleted_at": None,
        }
        conn.fetch.return_value = [
            {"id": target_id},
            {"id": uuid4()},  # other versions in chain
        ]
        now = datetime.now(tz=UTC)
        conn.fetchval.return_value = now
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert output.cascaded_count == 1  # one additional version

    @pytest.mark.asyncio
    async def test_delete_versioned_promotes_prior(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deleting current version of versioned type promotes prior version."""
        tool = MemoryDeleteTool()
        ctx = _build_ctx()
        target_id = uuid4()
        prior_id = uuid4()
        inp = MemoryDeleteInput(id=target_id, cascade=False)

        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            # Initial lookup
            {
                "id": target_id,
                "type": "decision",  # versioned
                "supersedes": prior_id,
                "is_current": True,
                "deleted_at": None,
            },
            # Lookup prior version (should exist and be live)
            {"id": prior_id},
        ]
        now = datetime.now(tz=UTC)
        conn.fetchval.return_value = now
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert output.promoted_version_id == prior_id

    @pytest.mark.asyncio
    async def test_delete_non_current_no_promotion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deleting non-current version doesn't trigger promotion logic."""
        tool = MemoryDeleteTool()
        ctx = _build_ctx()
        target_id = uuid4()
        inp = MemoryDeleteInput(id=target_id, cascade=False)

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": target_id,
            "type": "decision",  # versioned, but not current
            "supersedes": uuid4(),
            "is_current": False,
            "deleted_at": None,
        }
        now = datetime.now(tz=UTC)
        conn.fetchval.return_value = now
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert output.promoted_version_id is None

    @pytest.mark.asyncio
    async def test_delete_versioned_no_prior(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deleting current version with no prior doesn't promote."""
        tool = MemoryDeleteTool()
        ctx = _build_ctx()
        target_id = uuid4()
        inp = MemoryDeleteInput(id=target_id, cascade=False)

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": target_id,
            "type": "fact",  # versioned
            "supersedes": None,  # no prior
            "is_current": True,
            "deleted_at": None,
        }
        now = datetime.now(tz=UTC)
        conn.fetchval.return_value = now
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert output.promoted_version_id is None
