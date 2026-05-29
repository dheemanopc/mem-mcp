"""GET /api/web/notices — pull + render the caller's pending plugin notices.

Companion to the MCP transport's notice injection: chat clients see notices
woven into tool responses; the web dashboard (Next.js) calls this endpoint
to render a banner / inbox.

Same at-most-once delivery semantics as the MCP path: each GET marks the
returned notices as `delivered_at = now()` so a subsequent GET on the same
session won't re-show them. There is no separate "dismiss" endpoint
because rendering IS the dismiss.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Cookie, HTTPException

from mem_mcp.db import system_tx
from mem_mcp.plugins.notice_delivery import deliver_pending
from mem_mcp.web.sessions import lookup_session

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


def make_notices_router(*, pool: asyncpg.Pool) -> APIRouter:
    """Factory for /api/web/notices router."""
    router = APIRouter()

    @router.get("/api/web/notices")
    async def get_notices(
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        ctx_session = await lookup_session(pool, mem_session)
        if ctx_session is None:
            raise HTTPException(status_code=401, detail="invalid session")
        async with system_tx(pool) as conn:
            notices = await deliver_pending(conn, ctx_session.tenant_id)
        return {"notices": [n.to_dict() for n in notices]}

    return router
