"""Concrete MemoryClient — wraps existing MCP tools for plugin use.

Plugins consume this via `ctx.memories`. They do NOT touch the memories DB
directly. RBAC + tenant scoping is enforced by the underlying tool layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]

from mem_mcp.mcp.tools.get import MemoryGetInput, MemoryGetTool
from mem_mcp.mcp.tools.search import MemorySearchInput, MemorySearchTool
from mem_mcp.mcp.tools.write import MemoryWriteInput, MemoryWriteTool

if TYPE_CHECKING:
    from mem_mcp.mcp.tools._deps import ToolDeps


class MemoryClientImpl:
    """asyncpg-backed memory client scoped to a (tenant_id, plugin_id) pair.

    Wraps the existing MCP tools (MemoryWriteTool, MemorySearchTool, etc.)
    to provide the MemoryClient Protocol interface for plugins. All RBAC,
    tenant scoping, auditing, and quota checks happen in the tools.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        tenant_id: UUID,
        plugin_id: str,
        deps: ToolDeps,
        identity_id: UUID,
    ) -> None:
        self._pool = pool
        self._tenant_id = tenant_id
        self._plugin_id = plugin_id
        self._deps = deps
        self._identity_id = identity_id

    async def write(
        self,
        content: str,
        *,
        type: str = "note",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UUID:
        """Write a memory (or merge into existing duplicate).

        Returns the memory UUID.
        """
        from mem_mcp.mcp.tools._base import ToolContext
        from mem_mcp.mcp.tools.write import MemoryWriteOutput

        ctx = ToolContext(
            request_id=self._plugin_id,  # plugin acts as its own request source
            tenant_id=self._tenant_id,
            identity_id=self._identity_id,
            client_id=f"plugin:{self._plugin_id}",
            scopes=frozenset(["memory.write"]),
            db_pool=self._pool,
            deps=self._deps,
        )
        inp = MemoryWriteInput(
            content=content,
            type=type,  # type: ignore[arg-type]
            tags=tags or [],
            metadata=metadata or {},
        )
        tool = MemoryWriteTool()
        out = await tool(ctx, inp)
        assert isinstance(out, MemoryWriteOutput)
        return out.id

    async def search(
        self,
        query: str,
        *,
        type: str | None = None,
        tags: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search memories by query + optional filters.

        Returns a list of memory records (dict with id, content, type, tags, etc.).
        """
        from mem_mcp.mcp.tools._base import ToolContext
        from mem_mcp.mcp.tools.search import MemorySearchOutput

        ctx = ToolContext(
            request_id=self._plugin_id,
            tenant_id=self._tenant_id,
            identity_id=self._identity_id,
            client_id=f"plugin:{self._plugin_id}",
            scopes=frozenset(["memory.read"]),
            db_pool=self._pool,
            deps=self._deps,
        )
        inp = MemorySearchInput(
            query=query,
            type=type,  # type: ignore[arg-type]
            tags=tags or [],
            since=since,
            until=until,
            limit=limit,
        )
        tool = MemorySearchTool()
        out = await tool(ctx, inp)
        assert isinstance(out, MemorySearchOutput)
        # Convert output records to dicts
        return [record.model_dump(mode="json") for record in out.results]

    async def get(self, memory_id: UUID) -> dict[str, Any] | None:
        """Fetch a single memory by id.

        Returns the memory record dict or None if not found.
        """
        from mem_mcp.mcp.errors import JsonRpcError
        from mem_mcp.mcp.tools._base import ToolContext
        from mem_mcp.mcp.tools.get import MemoryGetOutput

        ctx = ToolContext(
            request_id=self._plugin_id,
            tenant_id=self._tenant_id,
            identity_id=self._identity_id,
            client_id=f"plugin:{self._plugin_id}",
            scopes=frozenset(["memory.read"]),
            db_pool=self._pool,
            deps=self._deps,
        )
        inp = MemoryGetInput(id=memory_id, include_history=False)
        tool = MemoryGetTool()
        try:
            out = await tool(ctx, inp)
            assert isinstance(out, MemoryGetOutput)
            return out.memory.model_dump(mode="json")
        except JsonRpcError as e:
            if "not found" in e.message.lower():
                return None
            raise

    async def supersede(self, memory_id: UUID, content: str) -> UUID:
        """Supersede a memory (decision or fact only).

        Returns the new (superseding) memory UUID.
        """
        from mem_mcp.mcp.tools._base import ToolContext
        from mem_mcp.mcp.tools.write import MemoryWriteOutput

        ctx = ToolContext(
            request_id=self._plugin_id,
            tenant_id=self._tenant_id,
            identity_id=self._identity_id,
            client_id=f"plugin:{self._plugin_id}",
            scopes=frozenset(["memory.write"]),
            db_pool=self._pool,
            deps=self._deps,
        )
        # Create new memory with supersedes=memory_id to create a new version
        inp = MemoryWriteInput(
            content=content,
            supersedes=memory_id,
        )
        tool = MemoryWriteTool()
        out = await tool(ctx, inp)
        assert isinstance(out, MemoryWriteOutput)
        return out.id
