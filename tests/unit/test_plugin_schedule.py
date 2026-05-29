"""Unit tests for plugin schedule → systemd timer translator."""

from __future__ import annotations

import pytest

from mem_mcp.plugins.schedule import ScheduleParseError, schedule_to_timer_body


class TestEveryNUnits:
    def test_every_60_seconds(self) -> None:
        out = schedule_to_timer_body("every 60 seconds")
        assert "OnUnitActiveSec=60s" in out
        assert "OnBootSec=60s" in out
        assert "Persistent=true" in out

    def test_every_1_second_singular(self) -> None:
        out = schedule_to_timer_body("every 1 second")
        assert "OnUnitActiveSec=1s" in out

    def test_every_5_minutes(self) -> None:
        out = schedule_to_timer_body("every 5 minutes")
        assert "OnUnitActiveSec=5min" in out

    def test_every_2_hours(self) -> None:
        out = schedule_to_timer_body("every 2 hours")
        assert "OnUnitActiveSec=2h" in out

    def test_case_insensitive(self) -> None:
        out = schedule_to_timer_body("EVERY 30 SECONDS")
        assert "OnUnitActiveSec=30s" in out

    def test_zero_interval_rejected(self) -> None:
        with pytest.raises(ScheduleParseError, match="must be >= 1"):
            schedule_to_timer_body("every 0 seconds")


class TestKeywordSchedules:
    def test_daily(self) -> None:
        out = schedule_to_timer_body("@daily")
        assert "OnCalendar=daily" in out
        assert "Persistent=true" in out

    def test_hourly(self) -> None:
        out = schedule_to_timer_body("@hourly")
        assert "OnCalendar=hourly" in out


class TestRejections:
    def test_unknown_schedule_string(self) -> None:
        with pytest.raises(ScheduleParseError, match="unrecognized"):
            schedule_to_timer_body("every full moon")

    def test_cron_syntax_not_supported(self) -> None:
        with pytest.raises(ScheduleParseError):
            schedule_to_timer_body("0 */2 * * *")

    def test_empty_string(self) -> None:
        with pytest.raises(ScheduleParseError):
            schedule_to_timer_body("")
