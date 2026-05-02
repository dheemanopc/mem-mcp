"""Tests for memory.list tool (T-7.1)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from mem_mcp.mcp.tools.list import MemoryListInput, MemoryListOutput, MemoryListTool
from mem_mcp.mcp.tools._base import ToolContext
from unittest.mock import AsyncMock, MagicMock


def _ctx() -> ToolContext:
    """Build a test ToolContext with mocked deps."""
    return ToolContext(
        request_id=str(uuid4()),
        tenant_id=uuid4(),
        identity_id=uuid4(),
        client_id="client-1",
        scopes=frozenset(("memory.read",)),
        db_pool=MagicMock(),
        deps=MagicMock(),
    )


def _memory_row(**overrides: Any) -> dict[str, Any]:
    """Build a canned memory row."""
    base = {
        "id": uuid4(),
        "content": "test memory",
        "type": "note",
        "tags": ["tag1", "tag2"],
        "version": 1,
        "is_current": True,
        "created_at": datetime.now(tz=UTC),
        "updated_at": datetime.now(tz=UTC),
        "deleted_at": None,
    }
    base.update(overrides)
    return base


def _encode_cursor(created_at: datetime, id_: str) -> str:
    """Encode a cursor from (created_at, id)."""
    return base64.urlsafe_b64encode(
        json.dumps([created_at.isoformat(), id_]).encode()
    ).decode()


def _decode_cursor(cursor: str) -> tuple[str, str]:
    """Decode a cursor back to (created_at_iso, id)."""
    data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    return data[0], data[1]


class TestMemoryListInput:
    def test_defaults(self) -> None:
        inp = MemoryListInput()
        assert inp.tags is None
        assert inp.type is None
        assert inp.since is None
        assert inp.until is None
        assert inp.include_deleted is False
        assert inp.include_history is False
        assert inp.order_by == "created_at"
        assert inp.order == "desc"
        assert inp.limit == 25
        assert inp.cursor is None

    def test_valid_type(self) -> None:
        inp = MemoryListInput(type="decision")
        assert inp.type == "decision"

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(ValueError):
            MemoryListInput(type="invalid")

    def test_limit_bounds(self) -> None:
        inp = MemoryListInput(limit=100)
        assert inp.limit == 100
        with pytest.raises(ValueError):
            MemoryListInput(limit=101)
        with pytest.raises(ValueError):
            MemoryListInput(limit=0)

    def test_order_values(self) -> None:
        inp = MemoryListInput(order="asc")
        assert inp.order == "asc"
        with pytest.raises(ValueError):
            MemoryListInput(order="invalid")

    def test_order_by_values(self) -> None:
        inp = MemoryListInput(order_by="updated_at")
        assert inp.order_by == "updated_at"
        with pytest.raises(ValueError):
            MemoryListInput(order_by="invalid")


class TestMemoryListCursorEncoding:
    def test_cursor_round_trip(self) -> None:
        dt = datetime(2025, 5, 1, 12, 0, 0, tzinfo=UTC)
        id_str = str(uuid4())
        cursor = _encode_cursor(dt, id_str)
        decoded_dt_iso, decoded_id = _decode_cursor(cursor)
        assert decoded_id == id_str
        assert decoded_dt_iso == dt.isoformat()

    def test_cursor_is_base64_encoded(self) -> None:
        dt = datetime.now(tz=UTC)
        id_str = str(uuid4())
        cursor = _encode_cursor(dt, id_str)
        # Should be base64-safe (no padding issues expected for valid JSON)
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=" for c in cursor)


class TestMemoryListTool:
    @pytest.mark.asyncio
    async def test_basic_fetch_no_filters(self) -> None:
        """Test basic fetch with no filters."""
        tool = MemoryListTool()
        ctx = _ctx()
        inp = MemoryListInput()

        # Mock conn.fetch to return 25 rows
        rows = [_memory_row(id=uuid4()) for _ in range(25)]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)
        mock_conn.fetchrow = AsyncMock(return_value=None)  # For audit

        ctx.deps.audit.audit = AsyncMock()

        # Mock tenant_tx context manager
        async def mock_tenant_tx(pool, tenant_id):  # type: ignore[no-untyped-def]
            class CM:  # type: ignore[no-name-defined]
                async def __aenter__(self) -> Any:
                    return mock_conn

                async def __aexit__(self, *args: Any) -> None:
                    pass

            return CM()

        # Patch tenant_tx
        import mem_mcp.mcp.tools.list as list_module
        original_tenant_tx = list_module.tenant_tx
        list_module.tenant_tx = mock_tenant_tx  # type: ignore[assignment]

        try:
            result = await tool(ctx, inp)
            assert isinstance(result, MemoryListOutput)
            assert len(result.results) == 25
            assert result.next_cursor is None
            assert result.request_id == ctx.request_id
        finally:
            list_module.tenant_tx = original_tenant_tx

    @pytest.mark.asyncio
    async def test_limit_plus_one_triggers_next_cursor(self) -> None:
        """Test that fetching limit+1 rows triggers next_cursor."""
        tool = MemoryListTool()
        ctx = _ctx()
        inp = MemoryListInput(limit=25)

        # Mock conn.fetch to return 26 rows (limit + 1)
        rows = [_memory_row(id=uuid4()) for _ in range(26)]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)

        ctx.deps.audit.audit = AsyncMock()

        async def mock_tenant_tx(pool, tenant_id):  # type: ignore[no-untyped-def]
            class CM:  # type: ignore[no-name-defined]
                async def __aenter__(self) -> Any:
                    return mock_conn

                async def __aexit__(self, *args: Any) -> None:
                    pass

            return CM()

        import mem_mcp.mcp.tools.list as list_module
        original_tenant_tx = list_module.tenant_tx
        list_module.tenant_tx = mock_tenant_tx  # type: ignore[assignment]

        try:
            result = await tool(ctx, inp)
            assert len(result.results) == 25  # Last row is dropped
            assert result.next_cursor is not None
        finally:
            list_module.tenant_tx = original_tenant_tx

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        """Test empty result set."""
        tool = MemoryListTool()
        ctx = _ctx()
        inp = MemoryListInput()

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        ctx.deps.audit.audit = AsyncMock()

        async def mock_tenant_tx(pool, tenant_id):  # type: ignore[no-untyped-def]
            class CM:  # type: ignore[no-name-defined]
                async def __aenter__(self) -> Any:
                    return mock_conn

                async def __aexit__(self, *args: Any) -> None:
                    pass

            return CM()

        import mem_mcp.mcp.tools.list as list_module
        original_tenant_tx = list_module.tenant_tx
        list_module.tenant_tx = mock_tenant_tx  # type: ignore[assignment]

        try:
            result = await tool(ctx, inp)
            assert result.results == []
            assert result.next_cursor is None
        finally:
            list_module.tenant_tx = original_tenant_tx

    @pytest.mark.asyncio
    async def test_default_order_is_created_at_desc(self) -> None:
        """Test that default order is created_at desc."""
        tool = MemoryListTool()
        ctx = _ctx()
        inp = MemoryListInput()

        assert inp.order_by == "created_at"
        assert inp.order == "desc"

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        ctx.deps.audit.audit = AsyncMock()

        async def mock_tenant_tx(pool, tenant_id):  # type: ignore[no-untyped-def]
            class CM:  # type: ignore[no-name-defined]
                async def __aenter__(self) -> Any:
                    return mock_conn

                async def __aexit__(self, *args: Any) -> None:
                    pass

            return CM()

        import mem_mcp.mcp.tools.list as list_module
        original_tenant_tx = list_module.tenant_tx
        list_module.tenant_tx = mock_tenant_tx  # type: ignore[assignment]

        try:
            result = await tool(ctx, inp)
            # Just verify it doesn't raise
            assert isinstance(result, MemoryListOutput)
        finally:
            list_module.tenant_tx = original_tenant_tx

    def test_output_model_structure(self) -> None:
        """Test MemoryListOutput structure."""
        row = _memory_row()
        from mem_mcp.mcp.tools.list import MemoryListItem

        item = MemoryListItem(**row)
        output = MemoryListOutput(
            results=[item],
            next_cursor=None,
            request_id="req-id",
        )
        assert len(output.results) == 1
        assert output.results[0].content == "test memory"
        assert output.next_cursor is None
        assert output.request_id == "req-id"
