"""Scheduled job to fire due reminders."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mem_mcp.db.tenant_tx import system_tx
from mem_mcp.plugins.clients.notices import NoticeClientImpl
from mem_mcp.skills.reminders import repository

if TYPE_CHECKING:
    from mem_mcp.plugins.contract import PluginContext

log = logging.getLogger(__name__)


async def fire_due_reminders_job(ctx: PluginContext) -> None:
    """Find all reminders whose fire condition is met; queue notices and mark fired.

    This job runs cross-tenant (not scoped to a single tenant), so we fetch all due
    items and process them individually with per-tenant notice queuing.
    """
    async with system_tx(ctx.pool) as conn:
        due = await repository.list_due_items(conn)

    for item in due:
        # Build a NoticeClient bound to the item's tenant
        notice = NoticeClientImpl(ctx.pool, item["tenant_id"], "reminders")
        try:
            await notice.queue(
                kind="reminders.due",
                template="Reminder: {content}",
                payload={"item_id": str(item["id"]), "content": item["content"]},
            )
            async with system_tx(ctx.pool) as conn:
                await repository.mark_fired(conn, item["id"])
                await repository.record_event(
                    conn,
                    item["id"],
                    "fired",
                    payload={"job": "fire_due_reminders"},
                )
            log.info("reminder_fired", extra={"item_id": str(item["id"])})
        except Exception as e:
            log.exception(
                "reminder_fire_failed",
                extra={"item_id": str(item["id"]), "error": str(e)},
            )
            # Continue with next item on error
            continue
