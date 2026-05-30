"""Web endpoints for the dashboard's inbox banner.

Two surfaces:

- `GET /api/web/notices` — at-most-once queue items from
  `NoticeClient.queue()`. Each call drains and marks delivered. Right
  shape for "kite order filled" style alerts.
- `GET /api/web/pending-state` — sticky items from each plugin's
  `surface_pending_state(tenant_id, conn)` hook. Recomputed on every
  call; same source the MCP transport uses for in-chat sticky surfacing.
  Today the only contributor is the reminders plugin (returns pending
  reminders until snoozed / dismissed / resolved).

The dashboard's NoticesBanner component polls both and renders them
together.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Cookie, HTTPException, Request

from mem_mcp.db import system_tx
from mem_mcp.plugins.notice_delivery import deliver_pending
from mem_mcp.web.sessions import lookup_session

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


def make_notices_router(*, pool: asyncpg.Pool) -> APIRouter:
    """Factory for /api/web/notices + /api/web/pending-state."""
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

    @router.get("/api/web/pending-state")
    async def get_pending_state(
        request: Request,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, list[str]]:
        """Aggregate each plugin's surface_pending_state for the caller's tenant."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        ctx_session = await lookup_session(pool, mem_session)
        if ctx_session is None:
            raise HTTPException(status_code=401, detail="invalid session")

        plugin_registry = getattr(request.app.state, "plugin_registry", None)
        items: list[str] = []
        if plugin_registry is not None:
            async with system_tx(pool) as conn:
                for plugin in plugin_registry.all():
                    try:
                        contributions = await plugin.surface_pending_state(
                            ctx_session.tenant_id, conn
                        )
                    except Exception:
                        # One plugin's failure must not break the inbox.
                        continue
                    items.extend(contributions)
        return {"items": items}

    return router
