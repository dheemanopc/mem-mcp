"""Memory CRUD handlers — wraps memory.list/get/update/delete/undelete tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
from fastapi import APIRouter, Body, Cookie, HTTPException, Query

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

    return router
