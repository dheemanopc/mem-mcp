"""Memory CRUD + management handlers — wraps memory.list/get/update/delete/undelete tools.

Phase-1 management additions (2026-05-19):
- GET  /api/web/memories/tags          — distinct tag autocomplete
- GET  /api/web/memories/stale         — stale-memory discovery (no UI bump)
- POST /api/web/memories/bulk-delete   — soft-delete a list of IDs in one tx
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
from fastapi import APIRouter, Body, Cookie, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._deps import ToolDeps
from mem_mcp.mcp.tools.delete import MemoryDeleteInput, MemoryDeleteTool
from mem_mcp.mcp.tools.get import MemoryGetInput, MemoryGetTool
from mem_mcp.mcp.tools.list import MemoryListInput, MemoryListTool
from mem_mcp.mcp.tools.undelete import MemoryUndeleteInput, MemoryUndeleteTool
from mem_mcp.mcp.tools.update import MemoryUpdateInput, MemoryUpdateTool
from mem_mcp.web.sessions import lookup_session

if TYPE_CHECKING:
    pass


async def _ctx_from_session(
    pool: asyncpg.Pool,
    mem_session: str | None,
    deps: ToolDeps,
    *,
    scopes: frozenset[str],
) -> ToolContext:
    """Resolve session and build ToolContext. Raises HTTPException on auth failure."""
    if not mem_session:
        raise HTTPException(status_code=401, detail="not authenticated")
    sess = await lookup_session(pool, mem_session)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid session")
    return ToolContext(
        request_id=str(uuid4()),
        tenant_id=sess.tenant_id,
        identity_id=sess.identity_id,
        client_id="web",
        scopes=scopes,
        db_pool=pool,
        deps=deps,
    )


def _jsonrpc_to_http(exc: JsonRpcError) -> HTTPException:
    """Map JsonRpcError to HTTP status."""
    status = 400
    if exc.code == -32602:
        status = 404 if exc.data and "not found" in exc.message.lower() else 400
    elif exc.code == -32000:
        status = 403  # scope errors, quota, etc.
    elif exc.code == -32603:
        status = 500
    return HTTPException(
        status_code=status, detail={"code": exc.code, "message": exc.message, "data": exc.data}
    )


def make_memories_router(*, pool: asyncpg.Pool, deps: ToolDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/web/memories")
    async def list_memories(
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        type: str | None = Query(default=None),
        tag: list[str] | None = Query(default=None),  # noqa: B008
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        ctx = await _ctx_from_session(pool, mem_session, deps, scopes=frozenset(["memory.read"]))
        inp_kwargs: dict[str, Any] = {"limit": limit}
        if cursor:
            inp_kwargs["cursor"] = cursor
        if type:
            inp_kwargs["type"] = type
        if tag:
            inp_kwargs["tags"] = tag
        try:
            out = await MemoryListTool()(ctx, MemoryListInput(**inp_kwargs))
        except JsonRpcError as e:
            raise _jsonrpc_to_http(e) from e
        return out.model_dump(mode="json")

    @router.get("/api/web/memories/{memory_id}")
    async def get_memory(
        memory_id: UUID,
        history: bool = Query(default=False),
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        ctx = await _ctx_from_session(pool, mem_session, deps, scopes=frozenset(["memory.read"]))
        try:
            out = await MemoryGetTool()(ctx, MemoryGetInput(id=memory_id, include_history=history))
        except JsonRpcError as e:
            raise _jsonrpc_to_http(e) from e
        return out.model_dump(mode="json")

    @router.patch("/api/web/memories/{memory_id}")
    async def update_memory(
        memory_id: UUID,
        body: dict[str, Any] = Body(...),  # noqa: B008
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        ctx = await _ctx_from_session(pool, mem_session, deps, scopes=frozenset(["memory.write"]))
        try:
            inp = MemoryUpdateInput(id=memory_id, **body)
            out = await MemoryUpdateTool()(ctx, inp)
        except JsonRpcError as e:
            raise _jsonrpc_to_http(e) from e
        return out.model_dump(mode="json")

    @router.delete("/api/web/memories/{memory_id}")
    async def delete_memory(
        memory_id: UUID,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        ctx = await _ctx_from_session(pool, mem_session, deps, scopes=frozenset(["memory.write"]))
        try:
            out = await MemoryDeleteTool()(ctx, MemoryDeleteInput(id=memory_id, cascade=False))
        except JsonRpcError as e:
            raise _jsonrpc_to_http(e) from e
        return out.model_dump(mode="json")

    @router.post("/api/web/memories/{memory_id}/undelete")
    async def undelete_memory(
        memory_id: UUID,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        ctx = await _ctx_from_session(pool, mem_session, deps, scopes=frozenset(["memory.write"]))
        try:
            out = await MemoryUndeleteTool()(ctx, MemoryUndeleteInput(id=memory_id))
        except JsonRpcError as e:
            raise _jsonrpc_to_http(e) from e
        return out.model_dump(mode="json")

    # ---- Management endpoints (Phase 1) ---------------------------------

    @router.get("/api/web/memories/management/tags")
    async def list_tags(
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, list[str]]:
        """Distinct tag values for the tenant — used by Browse-tab autocomplete."""
        ctx = await _ctx_from_session(pool, mem_session, deps, scopes=frozenset(["memory.read"]))
        async with tenant_tx(pool, ctx.tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT unnest(tags) AS tag
                FROM memories
                WHERE tenant_id = $1
                  AND deleted_at IS NULL
                  AND is_current = true
                ORDER BY tag
                """,
                ctx.tenant_id,
            )
        return {"tags": [r["tag"] for r in rows]}

    @router.get("/api/web/memories/management/stale")
    async def stale_memories(
        mode: Literal["updated", "accessed"] = Query(default="updated"),
        days: int = Query(default=90, ge=1, le=3650),
        limit: int = Query(default=50, ge=1, le=200),
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        """Memories not touched in N days. `mode=updated` uses updated_at (always
        populated); `mode=accessed` uses last_accessed_at (NULL means never read
        since the access-tracking column shipped). Reads here do NOT bump
        access-time — this is a management view, not normal traffic."""
        ctx = await _ctx_from_session(pool, mem_session, deps, scopes=frozenset(["memory.read"]))
        column = "updated_at" if mode == "updated" else "last_accessed_at"
        async with tenant_tx(pool, ctx.tenant_id) as conn:
            # NULL last_accessed_at means "never accessed since the column shipped",
            # which is the staleest possible — surface those first when mode=accessed.
            # Cutoff computed in Python so `days` doesn't have to be interpolated
            # into the SQL string.
            from datetime import datetime, timedelta

            cutoff = datetime.now(UTC) - timedelta(days=days)
            rows = await conn.fetch(
                f"""
                SELECT id, content, type, tags, created_at, updated_at,
                       last_accessed_at, access_count
                FROM memories
                WHERE tenant_id = $1
                  AND deleted_at IS NULL
                  AND is_current = true
                  AND ({column} IS NULL OR {column} < $2)
                ORDER BY {column} ASC NULLS FIRST
                LIMIT $3
                """,
                ctx.tenant_id,
                cutoff,
                limit,
            )
        return {
            "mode": mode,
            "days": days,
            "results": [
                {
                    "id": str(r["id"]),
                    "content": r["content"],
                    "type": r["type"],
                    "tags": list(r["tags"] or []),
                    "created_at": r["created_at"].isoformat(),
                    "updated_at": r["updated_at"].isoformat(),
                    "last_accessed_at": r["last_accessed_at"].isoformat()
                    if r["last_accessed_at"] is not None
                    else None,
                    "access_count": r["access_count"],
                }
                for r in rows
            ],
        }

    class _BulkDeleteBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        ids: list[UUID] = Field(min_length=1, max_length=500)

    @router.post("/api/web/memories/management/bulk-delete")
    async def bulk_delete(
        body: _BulkDeleteBody,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        """Soft-delete a list of memories. Uses the same code path as the
        single-row endpoint (sets deleted_at, hidden from search, recoverable
        via memory_undelete; retention job hard-deletes after 30 days)."""
        ctx = await _ctx_from_session(pool, mem_session, deps, scopes=frozenset(["memory.write"]))
        tool = MemoryDeleteTool()
        per_id: list[dict[str, Any]] = []
        success = 0
        for mid in body.ids:
            try:
                await tool(ctx, MemoryDeleteInput(id=mid, cascade=False))
                per_id.append({"id": str(mid), "status": "deleted"})
                success += 1
            except JsonRpcError as exc:
                per_id.append(
                    {
                        "id": str(mid),
                        "status": "failed",
                        "error": exc.message,
                        "code": exc.code,
                    }
                )
        return {"deleted_count": success, "results": per_id}

    return router
