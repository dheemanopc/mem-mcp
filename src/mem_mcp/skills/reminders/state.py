"""Reminders state machine. NO logic — just enum values."""

from enum import StrEnum


class ReminderState(StrEnum):
    """Possible states for a reminder."""

    ACTIVE = "active"
    SNOOZED = "snoozed"  # snooze-until is in the future
    DISMISSED = "dismissed"  # silenced; item still open
    RESOLVED = "resolved"  # done


def can_fire(state: str, snooze_until_ms: int | None, now_ms: int) -> bool:
    """Deterministic fire predicate. NO judgment, just boolean logic.

    Returns True if the reminder should fire:
    - State must be ACTIVE
    - No snooze, OR snooze timestamp has passed
    """
    if state != ReminderState.ACTIVE.value:
        return False
    if snooze_until_ms is not None and snooze_until_ms > now_ms:
        return False
    return True
