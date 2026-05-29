"""Pluck pending plugin notices for a tenant; render templates; mark delivered.

Called from the MCP transport on every successful tools/call to inject any
pending notices (e.g. reminders.due) into the response envelope. Also used
by the web /api/web/notices handler.

Delivery semantics: at-most-once. Once a notice's delivered_at is set,
it's never returned again. If the user's client crashed before rendering,
that notice is silently lost — acceptable for reminders (they're idempotent
in spirit: the next fire of the same reminder will queue a new notice).

Template format: `{key}` placeholders substituted from payload. Missing
keys render as empty string and log a warning — never crash delivery.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]

log = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class RenderedNotice:
    """One delivered notice as the user should see it."""

    __slots__ = ("id", "plugin_id", "kind", "text", "payload", "created_at")

    def __init__(
        self,
        *,
        id: UUID,
        plugin_id: str,
        kind: str,
        text: str,
        payload: dict[str, Any],
        created_at: datetime,
    ) -> None:
        self.id = id
        self.plugin_id = plugin_id
        self.kind = kind
        self.text = text
        self.payload = payload
        self.created_at = created_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "plugin_id": self.plugin_id,
            "kind": self.kind,
            "text": self.text,
            "payload": self.payload,
            "created_at": self.created_at.isoformat(),
        }


def _render(template: str, payload: dict[str, Any]) -> str:
    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in payload:
            log.warning(
                "notice_template_missing_key",
                extra={"key": key, "template": template[:120]},
            )
            return ""
        return str(payload[key])

    return _PLACEHOLDER_RE.sub(sub, template)


async def deliver_pending(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    *,
    limit: int = 20,
) -> list[RenderedNotice]:
    """Atomically claim + render pending notices for this tenant.

    Uses `UPDATE ... RETURNING` so concurrent calls won't double-deliver.
    Caller is responsible for the txn — typically use the same `system_tx`
    that surrounds the surrounding tool call's audit write.

    Returns up to `limit` rendered notices, oldest first. Empty list when
    none pending.
    """
    rows = await conn.fetch(
        """
        UPDATE plugin_notices
           SET delivered_at = now()
         WHERE id IN (
            SELECT id FROM plugin_notices
             WHERE tenant_id = $1
               AND delivered_at IS NULL
               AND expires_at > now()
             ORDER BY created_at ASC
             LIMIT $2
            FOR UPDATE SKIP LOCKED
         )
        RETURNING id, plugin_id, kind, template, payload, created_at
        """,
        tenant_id,
        limit,
    )
    out: list[RenderedNotice] = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            import json

            payload = json.loads(payload) if payload else {}
        text = _render(r["template"], payload)
        out.append(
            RenderedNotice(
                id=r["id"],
                plugin_id=r["plugin_id"],
                kind=r["kind"],
                text=text,
                payload=payload,
                created_at=r["created_at"],
            )
        )
    return out
