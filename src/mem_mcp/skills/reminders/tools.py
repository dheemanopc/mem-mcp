"""MCP tools for reminders plugin."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from mem_mcp.db.tenant_tx import system_tx
from mem_mcp.skills.reminders import repository
from mem_mcp.skills.reminders.state import ReminderState

# ── Input/Output Models ──


class CreateReminderInput(BaseModel):
    """Create a reminder."""

    content: str = Field(..., description="Reminder text")
    fire_at: datetime | None = Field(None, description="When to fire (ISO 8601 timestamp)")
    cadence: str | None = Field(
        None,
        description="Recurrence rule if repeating (reserved for future use)",
    )


class SnoozeInput(BaseModel):
    """Snooze a reminder."""

    item_id: UUID = Field(..., description="Reminder ID")
    until: datetime = Field(..., description="When to resume (ISO 8601 timestamp)")


class DismissInput(BaseModel):
    """Dismiss a reminder."""

    item_id: UUID = Field(..., description="Reminder ID")


class ResolveInput(BaseModel):
    """Resolve (complete) a reminder."""

    item_id: UUID = Field(..., description="Reminder ID")


class ListRemindersInput(BaseModel):
    """List reminders."""

    state: str | None = Field(
        None,
        description="Filter by state (active, snoozed, dismissed, resolved)",
    )
    limit: int = Field(50, description="Max results to return")


class GetReminderInput(BaseModel):
    """Get a single reminder."""

    item_id: UUID = Field(..., description="Reminder ID")


# ── Output Models ──


class ReminderOutput(BaseModel):
    """A reminder item."""

    id: UUID
    content: str
    fire_at: datetime | None
    cadence: str | None
    state: str
    snooze_until: datetime | None
    created_at: datetime
    updated_at: datetime
    dismissed_at: datetime | None
    resolved_at: datetime | None
    last_fired_at: datetime | None
    fire_count: int


class CreateReminderOutput(BaseModel):
    """Response from creating a reminder."""

    item_id: UUID
    state: str


class SnoozeOutput(BaseModel):
    """Response from snoozing."""

    item_id: UUID
    state: str
    snooze_until: datetime


class DismissOutput(BaseModel):
    """Response from dismissing."""

    item_id: UUID
    state: str


class ResolveOutput(BaseModel):
    """Response from resolving."""

    item_id: UUID
    state: str


class ListRemindersOutput(BaseModel):
    """Response from listing reminders."""

    items: list[ReminderOutput]


# ── Tool Handlers ──


async def create_reminder_handler(
    inp: CreateReminderInput,
    tenant_id: UUID,
    pool: asyncpg.Pool,
) -> CreateReminderOutput:
    """Create a new reminder."""
    async with system_tx(pool) as conn:
        item_id = await repository.create_item(
            conn,
            tenant_id,
            inp.content,
            inp.fire_at.isoformat() if inp.fire_at else None,
            inp.cadence,
        )
    return CreateReminderOutput(item_id=item_id, state=ReminderState.ACTIVE.value)


async def snooze_handler(
    inp: SnoozeInput,
    tenant_id: UUID,
    pool: asyncpg.Pool,
) -> SnoozeOutput:
    """Snooze a reminder."""
    async with system_tx(pool) as conn:
        item = await repository.get_item(conn, tenant_id, inp.item_id)
        if not item:
            raise ValueError(f"Reminder {inp.item_id} not found")

        # Only allowed if active
        if item["state"] != ReminderState.ACTIVE.value:
            raise ValueError(
                f"Cannot snooze reminder in state {item['state']}; only active reminders can be snoozed"
            )

        await repository.update_state(
            conn,
            tenant_id,
            inp.item_id,
            snooze_until=inp.until.isoformat(),
        )

    return SnoozeOutput(
        item_id=inp.item_id,
        state=ReminderState.SNOOZED.value,
        snooze_until=inp.until,
    )


async def dismiss_handler(
    inp: DismissInput,
    tenant_id: UUID,
    pool: asyncpg.Pool,
) -> DismissOutput:
    """Dismiss a reminder (silence it, but don't resolve)."""
    async with system_tx(pool) as conn:
        item = await repository.get_item(conn, tenant_id, inp.item_id)
        if not item:
            raise ValueError(f"Reminder {inp.item_id} not found")

        # Allow dismiss from active or snoozed
        if item["state"] not in (ReminderState.ACTIVE.value, ReminderState.SNOOZED.value):
            raise ValueError(
                f"Cannot dismiss reminder in state {item['state']}; only active or snoozed reminders can be dismissed"
            )

        await repository.update_state(
            conn,
            tenant_id,
            inp.item_id,
            state=ReminderState.DISMISSED.value,
            dismissed_at="now()",
        )

    return DismissOutput(item_id=inp.item_id, state=ReminderState.DISMISSED.value)


async def resolve_handler(
    inp: ResolveInput,
    tenant_id: UUID,
    pool: asyncpg.Pool,
) -> ResolveOutput:
    """Resolve (complete) a reminder."""
    async with system_tx(pool) as conn:
        item = await repository.get_item(conn, tenant_id, inp.item_id)
        if not item:
            raise ValueError(f"Reminder {inp.item_id} not found")

        # Allow resolve from any state except already resolved
        if item["state"] == ReminderState.RESOLVED.value:
            raise ValueError("Reminder is already resolved")

        await repository.update_state(
            conn,
            tenant_id,
            inp.item_id,
            state=ReminderState.RESOLVED.value,
            resolved_at="now()",
        )

    return ResolveOutput(item_id=inp.item_id, state=ReminderState.RESOLVED.value)


async def list_reminders_handler(
    inp: ListRemindersInput,
    tenant_id: UUID,
    pool: asyncpg.Pool,
) -> ListRemindersOutput:
    """List reminders for the tenant."""
    async with system_tx(pool) as conn:
        items = await repository.list_items(
            conn,
            tenant_id,
            state_filter=inp.state,
            limit=inp.limit,
        )

    return ListRemindersOutput(items=[ReminderOutput(**item) for item in items])


async def get_reminder_handler(
    inp: GetReminderInput,
    tenant_id: UUID,
    pool: asyncpg.Pool,
) -> ReminderOutput:
    """Get a single reminder."""
    async with system_tx(pool) as conn:
        item = await repository.get_item(conn, tenant_id, inp.item_id)
        if not item:
            raise ValueError(f"Reminder {inp.item_id} not found")

    return ReminderOutput(**item)
