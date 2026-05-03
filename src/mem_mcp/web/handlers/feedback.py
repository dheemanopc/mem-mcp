"""POST /api/web/feedback — wraps memory.feedback tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi import APIRouter, Body, Cookie, HTTPException
from pydantic import BaseModel, Field

from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._deps import ToolDeps
from mem_mcp.mcp.tools.feedback import MemoryFeedbackInput, MemoryFeedbackTool
from mem_mcp.web.sessions import lookup_session

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


class WebFeedbackBody(BaseModel):
    """Request body for POST /api/web/feedback."""

    text: str = Field(..., min_length=1, max_length=4096)
    metadata: dict[str, Any] = Field(default_factory=dict)


def make_feedback_router(*, pool: asyncpg.Pool, deps: ToolDeps) -> APIRouter:
    """Factory for /api/web/feedback router."""
    router = APIRouter()

    @router.post("/api/web/feedback")
    async def post_feedback(
        mem_session: str | None = Cookie(default=None),
        body: WebFeedbackBody = Body(...),  # noqa: B008
    ) -> dict[str, Any]:
        """Accept feedback from authenticated session."""
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
            scopes=frozenset(["memory.write"]),
            db_pool=pool,
            deps=deps,
        )
        tool = MemoryFeedbackTool()
        inp = MemoryFeedbackInput(text=body.text, metadata=body.metadata)
        out = await tool(tool_ctx, inp)
        return out.model_dump(mode="json")

    return router
