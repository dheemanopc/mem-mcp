"""Live-DB tests for reminders repository (gated on MEM_MCP_TEST_DSN)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest

from mem_mcp.db.tenant_tx import system_tx
from mem_mcp.skills.reminders import repository
from mem_mcp.skills.reminders.state import ReminderState

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
async def test_tenant_id(pg_pool: Any) -> AsyncIterator[UUID]:
    """Create a test tenant for the session."""
    tenant_id = uuid4()
    async with system_tx(pg_pool) as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            tenant_id,
            "test-tenant",
        )
    yield tenant_id
    # Cleanup: delete all reminders for this tenant
    async with system_tx(pg_pool) as conn:
        await conn.execute("DELETE FROM reminder_items WHERE tenant_id = $1", tenant_id)


@pytest.mark.integration
class TestRemindersRepository:
    """Test reminders repository with live DB."""

    async def test_create_and_get_item(self, pg_pool: Any, test_tenant_id: UUID) -> None:
        """Create and fetch a reminder item."""
        async with system_tx(pg_pool) as conn:
            item_id = await repository.create_item(
                conn,
                test_tenant_id,
                "Test reminder",
                None,
                None,
            )

            item = await repository.get_item(conn, test_tenant_id, item_id)
            assert item is not None
            assert item["id"] == item_id
            assert item["content"] == "Test reminder"
            assert item["state"] == ReminderState.ACTIVE.value
            assert item["tenant_id"] == test_tenant_id

    async def test_get_item_wrong_tenant(self, pg_pool: Any, test_tenant_id: UUID) -> None:
        """Getting an item from wrong tenant returns None."""
        other_tenant_id = uuid4()
        async with system_tx(pg_pool) as conn:
            await conn.execute(
                "INSERT INTO tenants (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                other_tenant_id,
                "other-tenant",
            )
            item_id = await repository.create_item(
                conn,
                test_tenant_id,
                "Test reminder",
                None,
                None,
            )
            # Try to fetch from other tenant
            item = await repository.get_item(conn, other_tenant_id, item_id)
            assert item is None

    async def test_list_items_filtered_by_state(self, pg_pool: Any, test_tenant_id: UUID) -> None:
        """List items filtered by state."""
        async with system_tx(pg_pool) as conn:
            id1 = await repository.create_item(conn, test_tenant_id, "Item 1", None, None)
            id2 = await repository.create_item(conn, test_tenant_id, "Item 2", None, None)

            # Dismiss one
            await repository.update_state(
                conn,
                test_tenant_id,
                id2,
                state=ReminderState.DISMISSED.value,
                dismissed_at="now()",
            )

            # List active only
            active = await repository.list_items(
                conn, test_tenant_id, state_filter=ReminderState.ACTIVE.value
            )
            assert len(active) == 1
            assert active[0]["id"] == id1

            # List dismissed
            dismissed = await repository.list_items(
                conn, test_tenant_id, state_filter=ReminderState.DISMISSED.value
            )
            assert len(dismissed) == 1
            assert dismissed[0]["id"] == id2

    async def test_update_state_records_event(self, pg_pool: Any, test_tenant_id: UUID) -> None:
        """State updates are recorded as events."""
        async with system_tx(pg_pool) as conn:
            item_id = await repository.create_item(conn, test_tenant_id, "Test", None, None)

            # Update to resolved
            await repository.update_state(
                conn,
                test_tenant_id,
                item_id,
                state=ReminderState.RESOLVED.value,
                resolved_at="now()",
            )

            # Check events
            events = await conn.fetch(
                "SELECT event_type FROM reminder_events WHERE item_id = $1 ORDER BY occurred_at",
                item_id,
            )
            assert len(events) >= 2  # created + state_changed_resolved
            event_types = [e["event_type"] for e in events]
            assert "created" in event_types
            assert "state_changed_resolved" in event_types

    async def test_list_due_items_excludes_snoozed_future(
        self, pg_pool: Any, test_tenant_id: UUID
    ) -> None:
        """list_due_items excludes items snoozed into the future."""
        now = datetime.now(UTC)
        future = (now + timedelta(hours=1)).isoformat()

        async with system_tx(pg_pool) as conn:
            id1 = await repository.create_item(
                conn, test_tenant_id, "Fire now", now.isoformat(), None
            )
            id2 = await repository.create_item(
                conn, test_tenant_id, "Fire now too", now.isoformat(), None
            )

            # Snooze one into the future
            await repository.update_state(conn, test_tenant_id, id2, snooze_until=future)

            # list_due_items (cross-tenant, no scoping)
            due = await repository.list_due_items(conn)
            due_ids = [item["id"] for item in due if item["tenant_id"] == test_tenant_id]
            assert id1 in due_ids
            assert id2 not in due_ids  # Snoozed, excluded

    async def test_list_due_items_excludes_dismissed_and_resolved(
        self, pg_pool: Any, test_tenant_id: UUID
    ) -> None:
        """list_due_items excludes dismissed and resolved items."""
        now = datetime.now(UTC)

        async with system_tx(pg_pool) as conn:
            id1 = await repository.create_item(
                conn, test_tenant_id, "Fire now", now.isoformat(), None
            )
            id2 = await repository.create_item(
                conn, test_tenant_id, "Dismissed", now.isoformat(), None
            )
            id3 = await repository.create_item(
                conn, test_tenant_id, "Resolved", now.isoformat(), None
            )

            # Dismiss and resolve
            await repository.update_state(
                conn,
                test_tenant_id,
                id2,
                state=ReminderState.DISMISSED.value,
                dismissed_at="now()",
            )
            await repository.update_state(
                conn,
                test_tenant_id,
                id3,
                state=ReminderState.RESOLVED.value,
                resolved_at="now()",
            )

            # list_due_items
            due = await repository.list_due_items(conn)
            due_ids = [item["id"] for item in due if item["tenant_id"] == test_tenant_id]
            assert id1 in due_ids
            assert id2 not in due_ids
            assert id3 not in due_ids

    async def test_tenant_isolation(self, pg_pool: Any, test_tenant_id: UUID) -> None:
        """Items from one tenant invisible to another."""
        other_tenant_id = uuid4()
        async with system_tx(pg_pool) as conn:
            await conn.execute(
                "INSERT INTO tenants (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                other_tenant_id,
                "other-tenant",
            )

            id1 = await repository.create_item(conn, test_tenant_id, "Item 1", None, None)
            id2 = await repository.create_item(conn, other_tenant_id, "Item 2", None, None)

            # List from first tenant
            items = await repository.list_items(conn, test_tenant_id)
            item_ids = [item["id"] for item in items]
            assert id1 in item_ids
            assert id2 not in item_ids

            # List from second tenant
            items = await repository.list_items(conn, other_tenant_id)
            item_ids = [item["id"] for item in items]
            assert id2 in item_ids
            assert id1 not in item_ids
