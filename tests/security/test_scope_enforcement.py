"""Tests for OAuth scope enforcement (T-6.7, spec S-5).

Verifies that the ToolRegistry and JWT validator properly enforce scope
checks at the registry dispatch level. A token lacking the required scope
for a tool must be rejected with a -32000 JsonRpcError before the tool is invoked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.registry import ToolRegistry
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._test_echo import EchoTool
from mem_mcp.mcp.tools.delete import MemoryDeleteTool
from mem_mcp.mcp.tools.export import MemoryExportTool
from mem_mcp.mcp.tools.feedback import MemoryFeedbackTool
from mem_mcp.mcp.tools.get import MemoryGetTool
from mem_mcp.mcp.tools.list import MemoryListTool
from mem_mcp.mcp.tools.search import MemorySearchTool
from mem_mcp.mcp.tools.stats import MemoryStatsTool
from mem_mcp.mcp.tools.supersede import MemorySupersedeTool
from mem_mcp.mcp.tools.undelete import MemoryUndeleteTool
from mem_mcp.mcp.tools.update import MemoryUpdateTool
from mem_mcp.mcp.tools.write import MemoryWriteTool


def _ctx(scopes: tuple[str, ...] = ("memory.read", "memory.write")) -> ToolContext:
    """Build a ToolContext with specified scopes."""
    return ToolContext(
        request_id=str(uuid4()),
        tenant_id=uuid4(),
        identity_id=uuid4(),
        client_id="client-1",
        scopes=frozenset(scopes),
        db_pool=MagicMock(),
        deps=None,
    )


class TestReadScopeEnforcement:
    """Read-only tools require memory.read scope."""

    @pytest.mark.security
    @pytest.mark.parametrize(
        "tool_class",
        [
            MemorySearchTool,
            MemoryGetTool,
            MemoryListTool,
            MemoryExportTool,
        ],
    )
    @pytest.mark.asyncio
    async def test_read_tool_rejected_without_memory_read(self, tool_class: type[Any]) -> None:
        """A token without memory.read cannot invoke a read-only tool."""
        registry = ToolRegistry()
        registry.register(tool_class)

        ctx = _ctx(scopes=())  # No scopes at all
        with pytest.raises(JsonRpcError) as exc_info:
            await registry.dispatch(ctx, tool_class.name, {})

        assert exc_info.value.code == -32000
        assert exc_info.value.data is not None
        assert exc_info.value.data["code"] == "insufficient_scope"

    @pytest.mark.security
    @pytest.mark.parametrize(
        "tool_class",
        [
            MemorySearchTool,
            MemoryGetTool,
            MemoryListTool,
            MemoryExportTool,
        ],
    )
    @pytest.mark.asyncio
    async def test_read_tool_rejected_with_only_write_scope(self, tool_class: type[Any]) -> None:
        """A token with only memory.write cannot invoke a read-only tool."""
        registry = ToolRegistry()
        registry.register(tool_class)

        ctx = _ctx(scopes=("memory.write",))
        with pytest.raises(JsonRpcError) as exc_info:
            await registry.dispatch(ctx, tool_class.name, {})

        assert exc_info.value.code == -32000
        assert exc_info.value.data is not None
        assert exc_info.value.data["code"] == "insufficient_scope"


class TestWriteScopeEnforcement:
    """Write tools require memory.write scope."""

    @pytest.mark.security
    @pytest.mark.parametrize(
        "tool_class",
        [
            MemoryWriteTool,
            MemoryUpdateTool,
            MemoryDeleteTool,
            MemoryUndeleteTool,
            MemorySupersedeTool,
            MemoryFeedbackTool,
        ],
    )
    @pytest.mark.asyncio
    async def test_write_tool_rejected_without_memory_write(self, tool_class: type[Any]) -> None:
        """A token without memory.write cannot invoke a write tool."""
        registry = ToolRegistry()
        registry.register(tool_class)

        ctx = _ctx(scopes=())  # No scopes
        with pytest.raises(JsonRpcError) as exc_info:
            await registry.dispatch(ctx, tool_class.name, {})

        assert exc_info.value.code == -32000
        assert exc_info.value.data is not None
        assert exc_info.value.data["code"] == "insufficient_scope"

    @pytest.mark.security
    @pytest.mark.parametrize(
        "tool_class",
        [
            MemoryWriteTool,
            MemoryUpdateTool,
            MemoryDeleteTool,
            MemoryUndeleteTool,
            MemorySupersedeTool,
            MemoryFeedbackTool,
        ],
    )
    async def test_write_tool_rejected_with_only_read_scope(self, tool_class: type[Any]) -> None:
        """A token with only memory.read cannot invoke a write tool."""
        registry = ToolRegistry()
        registry.register(tool_class)

        ctx = _ctx(scopes=("memory.read",))
        with pytest.raises(JsonRpcError) as exc_info:
            await registry.dispatch(ctx, tool_class.name, {})

        assert exc_info.value.code == -32000
        assert exc_info.value.data is not None
        assert exc_info.value.data["code"] == "insufficient_scope"


class TestStatsToolScopes:
    """Stats tool requires memory.read scope."""

    @pytest.mark.security
    @pytest.mark.asyncio
    async def test_stats_tool_requires_read_scope(self) -> None:
        """MemoryStatsTool requires memory.read."""
        registry = ToolRegistry()
        registry.register(MemoryStatsTool)

        ctx = _ctx(scopes=("memory.write",))  # Only write, no read
        with pytest.raises(JsonRpcError) as exc_info:
            await registry.dispatch(ctx, "memory.stats", {})

        assert exc_info.value.code == -32000
        assert exc_info.value.data is not None
        assert exc_info.value.data["code"] == "insufficient_scope"


class TestTestToolScopes:
    """The _test.echo tool requires memory.read scope."""

    @pytest.mark.security
    @pytest.mark.asyncio
    async def test_echo_tool_requires_read_scope(self) -> None:
        """EchoTool requires memory.read scope."""
        registry = ToolRegistry()
        registry.register(EchoTool)

        # Without memory.read, echo should fail
        ctx = _ctx(scopes=())
        with pytest.raises(JsonRpcError) as exc_info:
            await registry.dispatch(ctx, "_test.echo", {"message": "hi"})

        assert exc_info.value.code == -32000
        assert exc_info.value.data is not None
        assert exc_info.value.data["code"] == "insufficient_scope"

    @pytest.mark.security
    @pytest.mark.asyncio
    async def test_echo_tool_succeeds_with_read_scope(self) -> None:
        """With memory.read scope, echo tool works."""
        registry = ToolRegistry()
        registry.register(EchoTool)

        ctx = _ctx(scopes=("memory.read",))
        result = await registry.dispatch(ctx, "_test.echo", {"message": "hi"})
        assert result["echoed"] == "hi"
