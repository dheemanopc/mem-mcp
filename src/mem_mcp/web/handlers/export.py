"""GET /api/web/data/export — streams full tenant data dump (DPDP right-to-access)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import JSONResponse

from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._deps import ToolDeps
from mem_mcp.mcp.tools.export import MemoryExportInput, MemoryExportTool
from mem_mcp.web.sessions import lookup_session


def make_export_router(*, pool: asyncpg.Pool, deps: ToolDeps) -> APIRouter:
    """Factory for export data router."""
    router = APIRouter()

    @router.get("/api/web/data/export")
    async def export_data(mem_session: str | None = Cookie(default=None)) -> JSONResponse:
        """Return full tenant data dump as JSON attachment (DPDP right-to-access).

        Returns JSON response with Content-Disposition header for download.
        Includes all memories (current, history, soft-deleted) and audit log.
        """
        if not mem_session:
            raise HTTPException(401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(401, detail="invalid session")

        # memory.export requires memory.admin scope. For v1 web user is the tenant owner so we grant it.
        ctx = ToolContext(
            request_id=str(uuid4()),
            tenant_id=sess.tenant_id,
            identity_id=sess.identity_id,
            client_id="web",
            scopes=frozenset(["memory.read", "memory.write", "memory.admin"]),
            db_pool=pool,
            deps=deps,
        )
        try:
            out = await MemoryExportTool()(ctx, MemoryExportInput())
        except JsonRpcError as e:
            raise HTTPException(
                500 if e.code == -32603 else 400,
                detail={"code": e.code, "message": e.message},
            ) from e

        ts = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
        filename = f"mem-mcp-export-{sess.tenant_id}-{ts}.json"
        return JSONResponse(
            content=out.model_dump(mode="json"),
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return router
