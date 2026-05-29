"""Translate plugin SDK schedule strings to systemd timer directives.

Plugins declare schedules as strings: "every 60 seconds", "every 5 minutes",
"every 2 hours", "@daily", "@hourly". This module renders that into a
systemd [Timer] section body that the unit generator drops into a .timer
file.

Granularity bigger than @daily is rejected — those workloads belong in
cron-like jobs scheduled at a specific time, not the simple "every N" form
that plugins use today. If a plugin needs that shape, extend this module
later with explicit cron-like grammar.
"""

from __future__ import annotations

import re

_EVERY_RE = re.compile(r"^every\s+(\d+)\s+(seconds?|minutes?|hours?)$", re.IGNORECASE)


class ScheduleParseError(ValueError):
    """The schedule string isn't a shape this module knows how to translate."""


def schedule_to_timer_body(schedule: str) -> str:
    """Render a plugin schedule string as the body of a systemd [Timer] section.

    Returns the directive lines (without the `[Timer]` header), e.g.:
        OnUnitActiveSec=60s
        OnBootSec=60s
        Persistent=true
        RandomizedDelaySec=10

    Persistent=true is always set so missed fires (e.g. host was down) catch
    up on next boot. OnBootSec mirrors OnUnitActiveSec so the timer fires
    soon after boot rather than waiting for the first interval to elapse.

    Raises ScheduleParseError on unrecognized schedule strings.
    """
    s = schedule.strip()
    s_lower = s.lower()

    if s_lower == "@daily":
        return "OnCalendar=daily\nPersistent=true\nRandomizedDelaySec=300\n"
    if s_lower == "@hourly":
        return "OnCalendar=hourly\nPersistent=true\nRandomizedDelaySec=60\n"

    m = _EVERY_RE.match(s)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower().rstrip("s")
        if n < 1:
            raise ScheduleParseError(f"schedule interval must be >= 1, got {schedule!r}")
        # systemd unit names: s / min / h. Plugin says "seconds/minutes/hours".
        systemd_unit = {"second": "s", "minute": "min", "hour": "h"}[unit]
        interval = f"{n}{systemd_unit}"
        randomized = "10" if unit == "second" else "30"
        return (
            f"OnBootSec={interval}\n"
            f"OnUnitActiveSec={interval}\n"
            f"Persistent=true\n"
            f"RandomizedDelaySec={randomized}\n"
        )

    raise ScheduleParseError(
        f"unrecognized schedule {schedule!r}; "
        "supported: 'every N seconds|minutes|hours', '@daily', '@hourly'"
    )
