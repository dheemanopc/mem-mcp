"""Concrete MemoryClient — wraps existing MCP tools for plugin use.

Plugins consume this via `ctx.memories`. They do NOT touch the memories DB
directly. RBAC + tenant scoping is enforced by the underlying tool layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
from pydantic import ValidationError

from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools.get import MemoryGetInput, MemoryGetTool
from mem_mcp.mcp.tools.search import MemorySearchInput, MemorySearchTool
from mem_mcp.mcp.tools.write import MemoryWriteInput, MemoryWriteTool, ReferenceInput
from mem_mcp.plugins.contract import PluginValidationError

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

    def _build_ctx(self, *, scope: str) -> Any:
        """Build a ToolContext for this plugin at the given scope."""
        from mem_mcp.mcp.tools._base import ToolContext

        return ToolContext(
            request_id=self._plugin_id,
            tenant_id=self._tenant_id,
            identity_id=self._identity_id,
            client_id=f"plugin:{self._plugin_id}",
            scopes=frozenset([scope]),
            db_pool=self._pool,
            deps=self._deps,
        )

    async def _invoke_tool(self, tool: Any, ctx: Any, inp: Any) -> Any:
        """Invoke a substrate tool, converting structured errors to PluginValidationError.

        Per DA `336346ef` §2 (SF-D resolution): the dispatcher (registry.py)
        correctly converts pydantic.ValidationError and propagates JsonRpcError,
        but the SDK calls tools directly — bypassing the dispatcher. This
        wrapper restores structured-error visibility for the SDK path.

        Codes emitted (additive contract; see MemoryClient Protocol docstring):
          - "invalid_params"        — pydantic.ValidationError on input
          - "<from data['code']>"   — JsonRpcError with structured data["code"]
          - "jsonrpc_<n>"           — fallback when data["code"] missing
        IT-08 opacity preserved: the substrate emits identical messages for
        not-found vs no-access; the wrapper forwards them verbatim.
        """
        try:
            return await tool(ctx, inp)
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_input=False)
            first: dict[str, Any] = dict(errors[0]) if errors else {}
            raise PluginValidationError(
                code="invalid_params",
                message=str(first.get("msg") or "invalid input")[:300],
                data={"errors": errors},
            ) from exc
        except JsonRpcError as exc:
            data = exc.data or {}
            code = str(data.get("code") or f"jsonrpc_{exc.code}")
            raise PluginValidationError(
                code=code,
                message=exc.message,
                data=data,
            ) from exc

    async def write(
        self,
        content: str,
        *,
        type: str = "note",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        parent_id: UUID | None = None,
        indexable: bool = True,
        references: list[ReferenceInput] | None = None,
    ) -> UUID:
        """Write a memory (or merge into existing duplicate).

        Returns the memory UUID.

        Tier-1 extensions (per DA `336346ef`): `parent_id` for threaded leaves,
        `indexable` for search opt-out, `references` for cross-team task-graph
        spine writes. `references` rides the IT-08-honoring resolver — bad refs
        raise PluginValidationError(code="memory_not_accessible") opaquely.
        """
        from mem_mcp.mcp.tools.write import MemoryWriteOutput

        ctx = self._build_ctx(scope="memory.write")
        try:
            inp = MemoryWriteInput(
                content=content,
                type=type,  # type: ignore[arg-type]
                tags=tags or [],
                metadata=metadata or {},
                parent_id=parent_id,
                indexable=indexable,
                references=references,
            )
        except ValidationError as exc:
            # Input construction validation (e.g. slug_clue-on-note style — F-9).
            errors = exc.errors(include_url=False, include_input=False)
            first: dict[str, Any] = dict(errors[0]) if errors else {}
            raise PluginValidationError(
                code="invalid_params",
                message=str(first.get("msg") or "invalid input")[:300],
                data={"errors": errors},
            ) from exc
        tool = MemoryWriteTool()
        out = await self._invoke_tool(tool, ctx, inp)
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
        from mem_mcp.mcp.tools.search import MemorySearchOutput

        ctx = self._build_ctx(scope="memory.read")
        inp = MemorySearchInput(
            query=query,
            type=type,  # type: ignore[arg-type]
            tags=tags or [],
            since=since,
            until=until,
            limit=limit,
        )
        tool = MemorySearchTool()
        out = await self._invoke_tool(tool, ctx, inp)
        assert isinstance(out, MemorySearchOutput)
        return [record.model_dump(mode="json") for record in out.results]

    async def get(self, memory_id: UUID) -> dict[str, Any] | None:
        """Fetch a single memory by id.

        Returns the memory record dict or None if not found.

        BACK-COMPAT CARVE-OUT (per LLD): this method preserves its existing
        "return None on not-found" contract — does NOT route through
        `_invoke_tool` so the structured-error conversion does not apply here.
        Callers that need to distinguish other failures still get JsonRpcError.
        """
        from mem_mcp.mcp.tools.get import MemoryGetOutput

        ctx = self._build_ctx(scope="memory.read")
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
        from mem_mcp.mcp.tools.write import MemoryWriteOutput

        ctx = self._build_ctx(scope="memory.write")
        inp = MemoryWriteInput(
            content=content,
            supersedes=memory_id,
        )
        tool = MemoryWriteTool()
        out = await self._invoke_tool(tool, ctx, inp)
        assert isinstance(out, MemoryWriteOutput)
        return out.id

    async def thread_get(self, root_id: UUID) -> list[dict[str, Any]]:
        """Fetch a memory thread: root + all replies in chronological order.

        Returns a flat list with root first, then replies. Plugin may re-split
        by type if needed. If `root_id` refers to a reply (flat-hierarchy
        violation), raises PluginValidationError with the substrate's message.
        """
        from mem_mcp.mcp.tools.thread_get import (
            MemoryThreadGetInput,
            MemoryThreadGetOutput,
            MemoryThreadGetTool,
        )

        ctx = self._build_ctx(scope="memory.read")
        inp = MemoryThreadGetInput(root_id=root_id)
        tool = MemoryThreadGetTool()
        out = await self._invoke_tool(tool, ctx, inp)
        assert isinstance(out, MemoryThreadGetOutput)
        return [out.root.model_dump(mode="json")] + [
            r.model_dump(mode="json") for r in out.replies
        ]

    async def get_batch(
        self,
        requests: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Fetch N memories in one round-trip; mixed UUID + slug-tuple entries.

        Each request entry is one of:
          - {"id": UUID}
          - {"team_id": UUID, "resource_type": "decision" | "fact", "slug": str}

        Returns one result entry per request in submission order. Each entry is
        {"ok": bool, "memory": MemoryRecord | None, "error": {code, message} | None}.
        Per-entry not-found and no-access return identical opaque errors per IT-08.
        """
        from mem_mcp.mcp.tools.get_batch import (
            MemoryGetBatchInput,
            MemoryGetBatchOutput,
            MemoryGetBatchTool,
        )

        ctx = self._build_ctx(scope="memory.read")
        try:
            inp = MemoryGetBatchInput(requests=requests)  # type: ignore[arg-type]
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_input=False)
            first: dict[str, Any] = dict(errors[0]) if errors else {}
            raise PluginValidationError(
                code="invalid_params",
                message=str(first.get("msg") or "invalid input")[:300],
                data={"errors": errors},
            ) from exc
        tool = MemoryGetBatchTool()
        out = await self._invoke_tool(tool, ctx, inp)
        assert isinstance(out, MemoryGetBatchOutput)
        return [r.model_dump(mode="json") for r in out.results]

    async def list_memories(
        self,
        *,
        tags: list[str] | None = None,
        indexable: bool | None = None,
        team_id: UUID | None = None,
        type: str | None = None,
        parent_id: UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """List memories matching filters.

        Tier-1 extension (per DA `336346ef` §1): `indexable`, `team_id`,
        `parent_id` filters ride a strict additional AND on the underlying
        WHERE clause — RLS-safe, can only narrow visibility.
        """
        from mem_mcp.mcp.tools.list import MemoryListInput, MemoryListOutput, MemoryListTool

        ctx = self._build_ctx(scope="memory.read")
        inp = MemoryListInput(
            tags=tags,
            type=type,  # type: ignore[arg-type]
            since=since,
            until=until,
            team_id=team_id,
            indexable=indexable,
            parent_id=parent_id,
            limit=limit,
        )
        tool = MemoryListTool()
        out = await self._invoke_tool(tool, ctx, inp)
        assert isinstance(out, MemoryListOutput)
        return [r.model_dump(mode="json") for r in out.results]

    async def update(
        self,
        memory_id: UUID,
        *,
        content: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        tags_op: Literal["replace", "add", "remove"] = "replace",
        type: str | None = None,
    ) -> dict[str, Any]:
        """Update a memory in place.

        Per DA `336346ef` §3 (Option A): content edits on `decision`/`fact`
        types are REFUSED at the SDK layer with
        PluginValidationError(code="update_not_allowed_for_type"); use
        `supersede()` instead. Tags/metadata edits pass through unrestricted
        on all types. The pre-fetch fires ONLY when `content` is provided —
        the common tags-only path pays zero extra cost.
        """
        from mem_mcp.mcp.tools.get import MemoryGetOutput
        from mem_mcp.mcp.tools.update import (
            MemoryUpdateInput,
            MemoryUpdateOutput,
            MemoryUpdateTool,
        )
        from mem_mcp.memory.versioning import VERSIONED_TYPES

        if content is not None:
            ctx_read = self._build_ctx(scope="memory.read")
            get_inp = MemoryGetInput(id=memory_id, include_history=False)
            get_out = await self._invoke_tool(MemoryGetTool(), ctx_read, get_inp)
            assert isinstance(get_out, MemoryGetOutput)
            if get_out.memory.type in VERSIONED_TYPES:
                raise PluginValidationError(
                    code="update_not_allowed_for_type",
                    message=(
                        f"in-place content update not allowed for "
                        f"type={get_out.memory.type!r}; use supersede() to "
                        "create a new version"
                    ),
                    data={"memory_id": str(memory_id), "type": get_out.memory.type},
                )

        ctx_write = self._build_ctx(scope="memory.write")
        try:
            inp = MemoryUpdateInput(
                id=memory_id,
                content=content,
                type=type,  # type: ignore[arg-type]
                tags=tags,
                metadata=metadata,
                tags_op=tags_op,
            )
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_input=False)
            first: dict[str, Any] = dict(errors[0]) if errors else {}
            raise PluginValidationError(
                code="invalid_params",
                message=str(first.get("msg") or "invalid input")[:300],
                data={"errors": errors},
            ) from exc
        tool = MemoryUpdateTool()
        out = await self._invoke_tool(tool, ctx_write, inp)
        assert isinstance(out, MemoryUpdateOutput)
        return out.model_dump(mode="json")
