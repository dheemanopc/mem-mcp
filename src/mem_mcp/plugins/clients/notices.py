"""Stub NoticeClient — queues notices into a table, does NOT deliver them yet.

Real delivery (injection into MCP response, web banner rendering) is a later
PR. For now, plugins can call ctx.notices.queue() and the notice persists,
visible via a future admin UI.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]

from mem_mcp.db.tenant_tx import system_tx

log = logging.getLogger(__name__)


class NoticeClientImpl:
    """Tenant-scoped notice queue. Persists; delivery is TODO."""

    def __init__(self, pool: asyncpg.Pool, tenant_id: UUID, plugin_id: str) -> None:
        self._pool = pool
        self._tenant_id = tenant_id
        self._plugin_id = plugin_id

    async def queue(
        self,
        kind: str,
        template: str,
        payload: dict[str, Any],
        *,
        ttl_seconds: int = 86400,
    ) -> None:
        """Queue a notice for the user. Auto-expires after ttl_seconds."""
        async with system_tx(self._pool) as conn:
            await conn.execute(
                """
                INSERT INTO plugin_notices
                    (tenant_id, plugin_id, kind, template, payload, expires_at)
                VALUES ($1, $2, $3, $4, $5::jsonb, now() + ($6 || ' seconds')::interval)
                """,
                self._tenant_id,
                self._plugin_id,
                kind,
                template,
                json.dumps(payload),
                str(ttl_seconds),
            )
        log.info("plugin_notice_queued", extra={"plugin_id": self._plugin_id, "kind": kind})
