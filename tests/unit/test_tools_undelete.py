"""Tests for memory.undelete tool (T-7.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools.undelete import MemoryUndeleteInput, MemoryUndeleteOutput, MemoryUndeleteTool


class TestMemoryUndelete:
    """Tests for memory.undelete tool."""

    def _ctx(self, scopes: tuple[str, ...] = ("memory.write",)) -> ToolContext:
        """Build a mock ToolContext."""
        return ToolContext(
            request_id="req-1",
            tenant_id=uuid4(),
            identity_id=uuid4(),
            client_id="client-1",
            scopes=frozenset(scopes),
            db_pool=MagicMock(),
            deps=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_undelete_within_grace(self) -> None:
        """Undelete within 30-day grace window succeeds."""
        tool = MemoryUndeleteTool()
        ctx = self._ctx()
        target_id = uuid4()
        inp = MemoryUndeleteInput(id=target_id)

        deleted_at = datetime.now(tz=UTC) - timedelta(days=15)
        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            # Initial lookup
            {
                "id": target_id,
                "type": "note",  # non-versioned
                "deleted_at": deleted_at,
                "supersedes": None,
                "superseded_by": None,
                "is_current": False,
                "age": timedelta(days=15),
            },
            # After undelete update
            {
                "deleted_at": None,
                "is_current": True,
            },
        ]

        ctx.db_pool.acquire = AsyncMock()
        ctx.db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        ctx.db_pool.acquire.return_value.__aexit__ = AsyncMock()

        output = await tool(ctx, inp)
        assert isinstance(output, MemoryUndeleteOutput)
        assert output.id == target_id
        assert output.deleted_at is None
        assert output.is_current is True

    @pytest.mark.asyncio
    async def test_undelete_past_grace(self) -> None:
        """Undelete past 30-day grace raises -32000 cannot_undelete_after_grace_period."""
        tool = MemoryUndeleteTool()
        ctx = self._ctx()
        target_id = uuid4()
        inp = MemoryUndeleteInput(id=target_id)

        deleted_at = datetime.now(tz=UTC) - timedelta(days=31)
        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": target_id,
            "type": "note",
            "deleted_at": deleted_at,
            "supersedes": None,
            "superseded_by": None,
            "is_current": False,
            "age": timedelta(days=31),
        }

        ctx.db_pool.acquire = AsyncMock()
        ctx.db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        ctx.db_pool.acquire.return_value.__aexit__ = AsyncMock()

        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, inp)
        assert exc.value.code == -32000
        assert exc.value.data is not None
        assert exc.value.data.get("code") == "cannot_undelete_after_grace_period"

    @pytest.mark.asyncio
    async def test_undelete_not_deleted(self) -> None:
        """Undelete on non-deleted memory raises -32602."""
        tool = MemoryUndeleteTool()
        ctx = self._ctx()
        target_id = uuid4()
        inp = MemoryUndeleteInput(id=target_id)

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": target_id,
            "type": "note",
            "deleted_at": None,  # not deleted
            "supersedes": None,
            "superseded_by": None,
            "is_current": True,
            "age": timedelta(0),
        }

        ctx.db_pool.acquire = AsyncMock()
        ctx.db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        ctx.db_pool.acquire.return_value.__aexit__ = AsyncMock()

        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, inp)
        assert exc.value.code == -32602
        assert "not deleted" in exc.value.message.lower()

    @pytest.mark.asyncio
    async def test_undelete_not_found(self) -> None:
        """Undelete on non-existent memory raises -32602."""
        tool = MemoryUndeleteTool()
        ctx = self._ctx()
        target_id = uuid4()
        inp = MemoryUndeleteInput(id=target_id)

        conn = AsyncMock()
        conn.fetchrow.return_value = None

        ctx.db_pool.acquire = AsyncMock()
        ctx.db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        ctx.db_pool.acquire.return_value.__aexit__ = AsyncMock()

        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, inp)
        assert exc.value.code == -32602
        assert "not found" in exc.value.message.lower()

    @pytest.mark.asyncio
    async def test_undelete_versioned_with_conflicting_sibling(self) -> None:
        """Undelete versioned type with conflicting current sibling restores but not current."""
        tool = MemoryUndeleteTool()
        ctx = self._ctx()
        target_id = uuid4()
        other_id = uuid4()
        inp = MemoryUndeleteInput(id=target_id)

        deleted_at = datetime.now(tz=UTC) - timedelta(days=10)
        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            # Initial lookup
            {
                "id": target_id,
                "type": "decision",  # versioned
                "deleted_at": deleted_at,
                "supersedes": None,
                "superseded_by": other_id,
                "is_current": False,
                "age": timedelta(days=10),
            },
            # Check for conflicting current sibling (should find one)
            # fetchval returns count > 0
            # After undelete update
            {
                "deleted_at": None,
                "is_current": False,  # NOT promoted because conflict
            },
        ]
        conn.fetchval.return_value = 1

        ctx.db_pool.acquire = AsyncMock()
        ctx.db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        ctx.db_pool.acquire.return_value.__aexit__ = AsyncMock()

        output = await tool(ctx, inp)
        assert output.deleted_at is None
        assert output.is_current is False

    @pytest.mark.asyncio
    async def test_undelete_versioned_no_conflict(self) -> None:
        """Undelete versioned type with no current sibling restores as current."""
        tool = MemoryUndeleteTool()
        ctx = self._ctx()
        target_id = uuid4()
        inp = MemoryUndeleteInput(id=target_id)

        deleted_at = datetime.now(tz=UTC) - timedelta(days=10)
        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            # Initial lookup
            {
                "id": target_id,
                "type": "fact",  # versioned
                "deleted_at": deleted_at,
                "supersedes": uuid4(),
                "superseded_by": None,
                "is_current": False,
                "age": timedelta(days=10),
            },
            # Check for conflicting current sibling (none found)
            # After undelete update
            {
                "deleted_at": None,
                "is_current": True,  # promoted because no conflict
            },
        ]
        conn.fetchval.return_value = 0  # no conflicts

        ctx.db_pool.acquire = AsyncMock()
        ctx.db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        ctx.db_pool.acquire.return_value.__aexit__ = AsyncMock()

        output = await tool(ctx, inp)
        assert output.deleted_at is None
        assert output.is_current is True

    @pytest.mark.asyncio
    async def test_undelete_at_grace_boundary(self) -> None:
        """Undelete exactly 30 days after deletion succeeds."""
        tool = MemoryUndeleteTool()
        ctx = self._ctx()
        target_id = uuid4()
        inp = MemoryUndeleteInput(id=target_id)

        deleted_at = datetime.now(tz=UTC) - timedelta(days=30)
        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            {
                "id": target_id,
                "type": "note",
                "deleted_at": deleted_at,
                "supersedes": None,
                "superseded_by": None,
                "is_current": False,
                "age": timedelta(days=30),
            },
            {
                "deleted_at": None,
                "is_current": True,
            },
        ]

        ctx.db_pool.acquire = AsyncMock()
        ctx.db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        ctx.db_pool.acquire.return_value.__aexit__ = AsyncMock()

        output = await tool(ctx, inp)
        assert output.deleted_at is None
