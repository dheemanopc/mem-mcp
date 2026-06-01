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
        slug_clue: str | None = None,
        expires_at: datetime | None = None,
        ttl_seconds: int | None = None,
    ) -> UUID:
        """Write a memory (or merge into existing duplicate).

        Returns the memory UUID.

        Tier-1 extensions (per DA `336346ef`): `parent_id` for threaded leaves,
        `indexable` for search opt-out, `references` for cross-team task-graph
        spine writes. `references` rides the IT-08-honoring resolver — bad refs
        raise PluginValidationError(code="memory_not_accessible") opaquely.

        Tier-2 extensions (per DA `04da8d62`): `slug_clue` for minting slugs
        on decision/fact (rejected for other types via Pydantic validator);
        `expires_at` / `ttl_seconds` for TTL on ephemeral writes (mutually
        exclusive — substrate `_validate_expiry_conflict` enforces).
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
                slug_clue=slug_clue,
                expires_at=expires_at,
                ttl_seconds=ttl_seconds,
            )
        except ValidationError as exc:
            # Input construction validation (e.g. slug_clue-on-note — F-9;
            # expires_at + ttl_seconds mutual exclusion; past expires_at).
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
        return [out.root.model_dump(mode="json")] + [r.model_dump(mode="json") for r in out.replies]

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

    # ── Tier-2 SDK parity additions (per DA spec 988ba555 + 04da8d62) ──────

    async def refs_in(self, memory_id: UUID) -> list[dict[str, Any]]:
        """Memories that cite `memory_id` (backward/reverse citation graph).

        IT-08 opacity: cite-edges whose source is in an inaccessible team
        are filtered out substrate-side (verified `refs_in.py:49`). The SDK
        returns ONLY the accessible edge list (`out.refs`); the substrate's
        `accessible_count`/`inaccessible_count` aggregates are deliberately
        dropped at the SDK boundary per DA `04da8d62` §3.1 — exposing
        `inaccessible_count` would leak existence of cross-team cite-edges
        the caller cannot read. Operators needing raw counts use the
        substrate tool directly.

        SUBSTRATE-REALITY NOTE: DA spec named a `limit` param but
        `RefsInInput` (`refs_in.py:16`) accepts only `memory_id`. SDK
        signature matches HEAD; impl-response surfaces this for DA.
        """
        from mem_mcp.mcp.tools.refs_in import RefsInInput, RefsInOutput, RefsInTool

        ctx = self._build_ctx(scope="memory.read")
        inp = RefsInInput(memory_id=memory_id)
        tool = RefsInTool()
        out = await self._invoke_tool(tool, ctx, inp)
        assert isinstance(out, RefsInOutput)
        return [r.model_dump(mode="json") for r in out.refs]

    async def refs_out(self, memory_id: UUID) -> list[dict[str, Any]]:
        """Memories that `memory_id` cites (forward citation graph).

        Symmetric to `refs_in`. Same IT-08 opacity discipline — counts
        dropped at SDK boundary; only accessible edges returned. Same
        substrate-reality note: no `limit` param.
        """
        from mem_mcp.mcp.tools.refs_out import RefsOutInput, RefsOutOutput, RefsOutTool

        ctx = self._build_ctx(scope="memory.read")
        inp = RefsOutInput(memory_id=memory_id)
        tool = RefsOutTool()
        out = await self._invoke_tool(tool, ctx, inp)
        assert isinstance(out, RefsOutOutput)
        return [r.model_dump(mode="json") for r in out.refs]

    async def slug_lookup(
        self,
        *,
        team_id: UUID,
        resource_type: Literal["decision", "fact"],
        slug: str,
    ) -> dict[str, Any] | None:
        """Resolve a slug to its memory record (or None if not resolvable).

        Per IT-08 opaque contract: returns None for ALL non-resolution cases —
        nonexistent slug in an accessible team OR slug in an inaccessible team.
        Caller cannot distinguish; this is the contract.
        """
        from mem_mcp.mcp.tools.slug_lookup import (
            MemsysSlugLookupInput,
            MemsysSlugLookupOutput,
            MemsysSlugLookupTool,
        )

        ctx = self._build_ctx(scope="memory.read")
        inp = MemsysSlugLookupInput(
            team_id=team_id,
            resource_type=resource_type,
            slug=slug,
        )
        tool = MemsysSlugLookupTool()
        out = await self._invoke_tool(tool, ctx, inp)
        assert isinstance(out, MemsysSlugLookupOutput)
        # Substrate returns the all-null trio when not resolvable.
        # SDK collapses that to None per DA spec §1 Item #3.
        if out.memory_id is None:
            return None
        return out.model_dump(mode="json")

    async def write_async(
        self,
        content: str,
        *,
        type: str = "note",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        parent_id: UUID | None = None,
        indexable: bool = True,
        references: list[ReferenceInput] | None = None,
        slug_clue: str | None = None,
        expires_at: datetime | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Fire-and-forget memory write.

        Returns the substrate envelope dict with keys:
          - `request_id`: UUID for caller-side correlation
          - `queued_at`: submission timestamp
          - `estimated_consistency_by`: submission + ~5s

        NO read-your-own-write in the same session — if you need the new
        memory's id downstream in the same turn, use sync `write()` instead.
        The substrate's async-write drain has a ~5s eventual consistency
        target; persistence is NOT guaranteed before the SDK call returns.
        """
        from mem_mcp.mcp.tools.write_async import MemoryWriteAsyncOutput, MemoryWriteAsyncTool

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
                slug_clue=slug_clue,
                expires_at=expires_at,
                ttl_seconds=ttl_seconds,
            )
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_input=False)
            first: dict[str, Any] = dict(errors[0]) if errors else {}
            raise PluginValidationError(
                code="invalid_params",
                message=str(first.get("msg") or "invalid input")[:300],
                data={"errors": errors},
            ) from exc
        tool = MemoryWriteAsyncTool()
        out = await self._invoke_tool(tool, ctx, inp)
        assert isinstance(out, MemoryWriteAsyncOutput)
        return out.model_dump(mode="json")

    async def write_batch(
        self,
        memories: list[dict[str, Any]],
        *,
        on_error: Literal["continue", "fail_all"] = "continue",
    ) -> list[dict[str, Any]]:
        """Batch-write N memories in one round-trip.

        Returns one result entry per input dict. Each entry is the substrate's
        `_BatchEntryResult` dict-form. Aggregate metadata (`written_count`,
        `failed_count`, `request_id`) is deliberately dropped at the SDK
        boundary per DA `04da8d62` §3.1 (same discipline as refs counts) —
        per-entry result array is the SDK contract.

        Per-entry quota: each memory counts individually against
        writes_per_minute + embed_tokens_daily. Mid-batch quota exhaustion
        fails the remaining entries with per-entry quota_exceeded errors.

        Substrate-inherited bounds: memories list must be 1..200 entries.
        Empty list or 201-entry raises PluginValidationError(code="invalid_params").
        """
        from mem_mcp.mcp.tools.write_batch import (
            MemoryWriteBatchInput,
            MemoryWriteBatchOutput,
            MemoryWriteBatchTool,
        )

        ctx = self._build_ctx(scope="memory.write")
        try:
            inp = MemoryWriteBatchInput(memories=memories, on_error=on_error)  # type: ignore[arg-type]
        except ValidationError as exc:
            # Per-entry error path is nested (memories.0.content style);
            # passed through verbatim in data["errors"] per DA 04da8d62 ruling.
            errors = exc.errors(include_url=False, include_input=False)
            first: dict[str, Any] = dict(errors[0]) if errors else {}
            raise PluginValidationError(
                code="invalid_params",
                message=str(first.get("msg") or "invalid input")[:300],
                data={"errors": errors},
            ) from exc
        tool = MemoryWriteBatchTool()
        out = await self._invoke_tool(tool, ctx, inp)
        assert isinstance(out, MemoryWriteBatchOutput)
        return [r.model_dump(mode="json") for r in out.results]
