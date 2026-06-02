"""Tests for the containerized job scheduler (mem_mcp.jobs.scheduler).

The scheduler replaces systemd timers when running under Docker: it computes
the next fire time for each core maintenance job and each plugin-declared job,
then shells out to the existing job entrypoints. These tests cover the pure
scheduling logic (no subprocess, no DB).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mem_mcp.jobs.scheduler import (
    CronSchedule,
    IntervalSchedule,
    ScheduledJob,
    build_core_jobs,
    parse_plugin_schedule,
)


def _utc(y: int, mo: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


class TestCronSchedule:
    def test_daily_at_fixed_time_same_day(self) -> None:
        sched = CronSchedule(minute={30}, hour={22})
        # 21:00 → next fire is 22:30 same day
        assert sched.next_after(_utc(2026, 6, 2, 21, 0)) == _utc(2026, 6, 2, 22, 30)

    def test_daily_at_fixed_time_rolls_to_next_day(self) -> None:
        sched = CronSchedule(minute={30}, hour={22})
        # 23:00 → next fire is 22:30 the following day
        assert sched.next_after(_utc(2026, 6, 2, 23, 0)) == _utc(2026, 6, 3, 22, 30)

    def test_strictly_after_excludes_now(self) -> None:
        sched = CronSchedule(minute={30}, hour={22})
        # exactly 22:30 → next fire is tomorrow, not the current minute
        assert sched.next_after(_utc(2026, 6, 2, 22, 30)) == _utc(2026, 6, 3, 22, 30)

    def test_hourly_at_minute(self) -> None:
        sched = CronSchedule(minute={15})
        assert sched.next_after(_utc(2026, 6, 2, 9, 10)) == _utc(2026, 6, 2, 9, 15)
        assert sched.next_after(_utc(2026, 6, 2, 9, 20)) == _utc(2026, 6, 2, 10, 15)

    def test_every_n_minutes_via_step(self) -> None:
        sched = CronSchedule(minute=set(range(0, 60, 5)))
        assert sched.next_after(_utc(2026, 6, 2, 9, 2)) == _utc(2026, 6, 2, 9, 5)
        assert sched.next_after(_utc(2026, 6, 2, 9, 5)) == _utc(2026, 6, 2, 9, 10)

    def test_weekly_day_of_week(self) -> None:
        # Saturday 22:00 (Mon=0 .. Sun=6 → Sat=5)
        sched = CronSchedule(minute={0}, hour={22}, day_of_week={5})
        # 2026-06-02 is a Tuesday → next Saturday is 2026-06-06
        assert sched.next_after(_utc(2026, 6, 2, 12, 0)) == _utc(2026, 6, 6, 22, 0)


class TestIntervalSchedule:
    def test_next_is_now_plus_interval(self) -> None:
        sched = IntervalSchedule(seconds=300)
        now = _utc(2026, 6, 2, 9, 0)
        nxt = sched.next_after(now)
        assert (nxt - now).total_seconds() == 300


class TestParsePluginSchedule:
    def test_every_minutes(self) -> None:
        sched = parse_plugin_schedule("every 5 minutes")
        assert isinstance(sched, IntervalSchedule)
        assert sched.seconds == 300

    def test_every_seconds(self) -> None:
        sched = parse_plugin_schedule("every 60 seconds")
        assert isinstance(sched, IntervalSchedule)
        assert sched.seconds == 60

    def test_every_hours(self) -> None:
        sched = parse_plugin_schedule("every 2 hours")
        assert isinstance(sched, IntervalSchedule)
        assert sched.seconds == 7200

    def test_hourly(self) -> None:
        sched = parse_plugin_schedule("@hourly")
        assert isinstance(sched, CronSchedule)
        assert sched.minute == {0}

    def test_daily(self) -> None:
        sched = parse_plugin_schedule("@daily")
        assert isinstance(sched, CronSchedule)
        assert sched.minute == {0}
        assert sched.hour == {0}

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="unrecognized schedule"):
            parse_plugin_schedule("once in a while")


class TestBuildCoreJobs:
    def test_includes_known_core_jobs(self) -> None:
        jobs = build_core_jobs()
        names = {j.name for j in jobs}
        # Every job registered in mem_mcp.jobs.__main__ should be scheduled.
        from mem_mcp.jobs._runner import _JOBS

        assert names == set(_JOBS)

    def test_each_job_has_a_command_and_schedule(self) -> None:
        for job in build_core_jobs():
            assert isinstance(job, ScheduledJob)
            assert job.argv[-1] == job.name
            assert "mem_mcp.jobs" in job.argv
            assert hasattr(job.schedule, "next_after")
