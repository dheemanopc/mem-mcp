"""GET /api/web/stats — wraps memory.stats tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi import APIRouter, Cookie, HTTPException

from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._deps import ToolDeps
from mem_mcp.mcp.tools.stats import MemoryStatsInput, MemoryStatsTool
from mem_mcp.web.sessions import lookup_session

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


def make_stats_router(*, pool: asyncpg.Pool, deps: ToolDeps) -> APIRouter:
    """Factory for /api/web/stats router."""
    router = APIRouter()

    @router.get("/api/web/stats")
    async def get_stats(mem_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        """Return stats for the authenticated session."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        ctx_session = await lookup_session(pool, mem_session)
        if ctx_session is None:
            raise HTTPException(status_code=401, detail="invalid session")
        tool_ctx = ToolContext(
            request_id=str(uuid4()),
            tenant_id=ctx_session.tenant_id,
            identity_id=ctx_session.identity_id,
            client_id="web",
            scopes=frozenset(["memory.read"]),
            db_pool=pool,
            deps=deps,
        )
        tool = MemoryStatsTool()
        out = await tool(tool_ctx, MemoryStatsInput())
        return out.model_dump(mode="json")

    return router
