"""Data-access layer for reminders — pure SQL, no business logic."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]


async def create_item(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    content: str,
    fire_at: str | None,
    cadence: str | None,
) -> UUID:
    """Create a reminder item. Returns the new item UUID."""
    item_id: UUID = await conn.fetchval(
        """
        INSERT INTO reminder_items
            (tenant_id, content, fire_at, cadence, state)
        VALUES ($1, $2, $3, $4, 'active')
        RETURNING id
        """,
        tenant_id,
        content,
        fire_at,
        cadence,
    )
    await record_event(conn, item_id, "created", payload={"content": content})
    return item_id


async def get_item(
    conn: asyncpg.Connection, tenant_id: UUID, item_id: UUID
) -> dict[str, Any] | None:
    """Fetch a single reminder item. Raises if item belongs to other tenant."""
    row = await conn.fetchrow(
        """
        SELECT id, tenant_id, content, fire_at, cadence, state,
               snooze_until, created_at, updated_at, dismissed_at, resolved_at,
               last_fired_at, fire_count
        FROM reminder_items
        WHERE id = $1 AND tenant_id = $2
        """,
        item_id,
        tenant_id,
    )
    return dict(row) if row else None


async def list_items(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    state_filter: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List reminder items for a tenant, optionally filtered by state."""
    if state_filter:
        rows = await conn.fetch(
            """
            SELECT id, tenant_id, content, fire_at, cadence, state,
                   snooze_until, created_at, updated_at, dismissed_at, resolved_at,
                   last_fired_at, fire_count
            FROM reminder_items
            WHERE tenant_id = $1 AND state = $2
            ORDER BY created_at DESC
            LIMIT $3
            """,
            tenant_id,
            state_filter,
            limit,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT id, tenant_id, content, fire_at, cadence, state,
                   snooze_until, created_at, updated_at, dismissed_at, resolved_at,
                   last_fired_at, fire_count
            FROM reminder_items
            WHERE tenant_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            tenant_id,
            limit,
        )
    return [dict(row) for row in rows]


async def update_state(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    item_id: UUID,
    *,
    state: str | None = None,
    snooze_until: str | None = None,
    dismissed_at: str | None = None,
    resolved_at: str | None = None,
) -> bool:
    """Update an item's state. Records an event for the transition. Returns True if updated."""
    # Build dynamic update
    updates = []
    params: list[Any] = [item_id, tenant_id]

    if state is not None:
        updates.append("state = $3")
        params.append(state)

    if snooze_until is not None:
        updates.append(f"snooze_until = ${len(params) + 1}")
        params.append(snooze_until)

    if dismissed_at is not None:
        updates.append(f"dismissed_at = ${len(params) + 1}")
        params.append(dismissed_at)

    if resolved_at is not None:
        updates.append(f"resolved_at = ${len(params) + 1}")
        params.append(resolved_at)

    if not updates:
        return False

    updates.append(f"updated_at = ${len(params) + 1}")
    params.append("now()")

    query = f"""
        UPDATE reminder_items
        SET {', '.join(updates)}
        WHERE id = $1 AND tenant_id = $2
        RETURNING id
    """

    result = await conn.fetchval(query, *params)
    if result:
        # Record the event
        event_payload = {}
        if state is not None:
            event_payload["new_state"] = state
        if snooze_until is not None:
            event_payload["snooze_until"] = snooze_until
        if dismissed_at is not None:
            event_payload["dismissed_at"] = dismissed_at
        if resolved_at is not None:
            event_payload["resolved_at"] = resolved_at

        # Infer event type
        if state is not None:
            event_type = f"state_changed_{state}"
        elif snooze_until is not None:
            event_type = "snoozed"
        elif dismissed_at is not None:
            event_type = "dismissed"
        elif resolved_at is not None:
            event_type = "resolved"
        else:
            event_type = "updated"

        await record_event(conn, item_id, event_type, payload=event_payload)
        return True
    return False


async def list_due_items(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    """Cross-tenant: fetch all reminders whose fire condition is met.

    Used by the scheduler job. No tenant scoping.
    """
    rows = await conn.fetch(
        """
        SELECT id, tenant_id, content, fire_at, cadence, state,
               snooze_until, created_at, updated_at, dismissed_at, resolved_at,
               last_fired_at, fire_count
        FROM reminder_items
        WHERE state = 'active'
          AND (snooze_until IS NULL OR snooze_until < now())
          AND (fire_at IS NULL OR fire_at <= now())
        """
    )
    return [dict(row) for row in rows]


async def record_event(
    conn: asyncpg.Connection,
    item_id: UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Record an event in the audit log."""
    await conn.execute(
        """
        INSERT INTO reminder_events (item_id, event_type, payload)
        VALUES ($1, $2, $3::jsonb)
        """,
        item_id,
        event_type,
        json.dumps(payload) if payload else None,
    )


async def mark_fired(conn: asyncpg.Connection, item_id: UUID) -> None:
    """Mark an item as fired: bump last_fired_at + fire_count."""
    await conn.execute(
        """
        UPDATE reminder_items
        SET last_fired_at = now(), fire_count = fire_count + 1
        WHERE id = $1
        """,
        item_id,
    )
