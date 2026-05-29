"""Unit tests for reminders state machine."""

from mem_mcp.skills.reminders.state import ReminderState, can_fire


class TestCanFire:
    """Test the deterministic fire predicate."""

    def test_can_fire_active_no_snooze(self) -> None:
        """Active with no snooze should fire."""
        assert can_fire(ReminderState.ACTIVE.value, snooze_until_ms=None, now_ms=1000)

    def test_can_fire_active_snoozed_future(self) -> None:
        """Active but snoozed into the future should NOT fire."""
        assert not can_fire(
            ReminderState.ACTIVE.value, snooze_until_ms=2000, now_ms=1000
        )

    def test_can_fire_active_snoozed_past(self) -> None:
        """Active but snooze in the past should fire."""
        assert can_fire(
            ReminderState.ACTIVE.value, snooze_until_ms=500, now_ms=1000
        )

    def test_can_fire_dismissed_returns_false(self) -> None:
        """Dismissed reminders never fire."""
        assert not can_fire(
            ReminderState.DISMISSED.value, snooze_until_ms=None, now_ms=1000
        )

    def test_can_fire_resolved_returns_false(self) -> None:
        """Resolved reminders never fire."""
        assert not can_fire(
            ReminderState.RESOLVED.value, snooze_until_ms=None, now_ms=1000
        )

    def test_can_fire_snoozed_returns_false(self) -> None:
        """Snoozed state (not active) never fires."""
        assert not can_fire(
            ReminderState.SNOOZED.value, snooze_until_ms=None, now_ms=1000
        )
