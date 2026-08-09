"""In-process scheduler for maintenance + plugin jobs (container deployments).

systemd timers drive the jobs on the EC2/systemd deployment (see
``deploy/systemd/*.timer`` and ``deploy/scripts/generate_plugin_units.py``).
Containers have no systemd, so the ``jobs`` service runs this scheduler
instead: it computes the next fire time for every core job and every
plugin-declared job, sleeps until the earliest, and shells out to the same
entrypoints (``python -m mem_mcp.jobs <name>`` /
``python -m mem_mcp.jobs.plugin_job <id> <name>``).

Run it with:  ``python -m mem_mcp.jobs.scheduler``

Design notes:
  - Each job runs as a fresh subprocess (mirrors systemd ``Type=oneshot``),
    so one job crashing never takes down the scheduler or sibling jobs.
  - Schedules are evaluated in UTC, matching ``OnCalendar=... UTC`` timers.
  - The cron evaluator is intentionally minute-granular and brute-forced
    (walk forward a minute at a time) — simple and correct; the few hundred
    iterations per computation are negligible.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

log = logging.getLogger("mem_mcp.jobs.scheduler")

# Look-ahead cap for the cron walk (8 days covers any weekly schedule).
_MAX_LOOKAHEAD_MINUTES = 8 * 24 * 60


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CronSchedule:
    """Calendar schedule. Unset fields (``None``) match any value.

    ``day_of_week`` uses Python's convention: Monday=0 .. Sunday=6.
    """

    minute: set[int] | None = None
    hour: set[int] | None = None
    day_of_week: set[int] | None = None

    def _matches(self, when: datetime) -> bool:
        if self.minute is not None and when.minute not in self.minute:
            return False
        if self.hour is not None and when.hour not in self.hour:
            return False
        if self.day_of_week is not None and when.weekday() not in self.day_of_week:
            return False
        return True

    def next_after(self, now: datetime) -> datetime:
        """Return the next firing strictly after ``now`` (minute resolution)."""
        # Advance to the start of the next minute so ``now`` itself never fires.
        candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(_MAX_LOOKAHEAD_MINUTES):
            if self._matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        raise RuntimeError("no matching time within look-ahead window (bad schedule?)")


@dataclass(frozen=True)
class IntervalSchedule:
    """Fixed-interval schedule: fire every ``seconds`` seconds."""

    seconds: int

    def next_after(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self.seconds)


Schedule = CronSchedule | IntervalSchedule


@dataclass
class ScheduledJob:
    """A job plus its schedule and the argv used to run it as a subprocess."""

    name: str
    schedule: Schedule
    argv: list[str]
    next_run: datetime = field(default=datetime.min.replace(tzinfo=UTC))


# ---------------------------------------------------------------------------
# Plugin schedule parsing (mirrors mem_mcp.plugins.schedule grammar)
# ---------------------------------------------------------------------------

_EVERY_RE = re.compile(r"^every\s+(\d+)\s+(seconds?|minutes?|hours?)$", re.IGNORECASE)
_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600}


def parse_plugin_schedule(schedule: str) -> Schedule:
    """Translate a plugin SDK schedule string into a Schedule.

    Supports the same grammar as ``mem_mcp.plugins.schedule``:
    ``"every N seconds|minutes|hours"``, ``"@hourly"``, ``"@daily"``.
    """
    s = schedule.strip()
    low = s.lower()
    if low == "@hourly":
        return CronSchedule(minute={0})
    if low == "@daily":
        return CronSchedule(minute={0}, hour={0})
    m = _EVERY_RE.match(s)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower().rstrip("s")
        if n < 1:
            raise ValueError(f"schedule interval must be >= 1, got {schedule!r}")
        return IntervalSchedule(seconds=n * _UNIT_SECONDS[unit])
    raise ValueError(
        f"unrecognized schedule {schedule!r}; "
        "supported: 'every N seconds|minutes|hours', '@hourly', '@daily'"
    )


# ---------------------------------------------------------------------------
# Job catalogs
# ---------------------------------------------------------------------------

# Core maintenance jobs, mirroring deploy/systemd/*.timer (all times UTC).
# The pg_dump backup timer is intentionally excluded — it's a shell/DB-host
# concern handled outside the Python jobs container.
_CORE_SCHEDULES: dict[str, CronSchedule] = {
    "chunk_build": CronSchedule(minute=set(range(0, 60, 5))),  # every 5 min
    "cleanup_clients": CronSchedule(minute={30}, hour={22}),
    "cleanup_kite_intents": CronSchedule(minute={30}, hour={21}),
    "cluster_build": CronSchedule(minute={0}, hour={22}, day_of_week={5}),  # Sat
    "embedding_backfill": CronSchedule(minute={15}),  # hourly at :15
    "expire_transient": CronSchedule(minute={45}, hour={22}),
    "reconcile_signups": CronSchedule(minute=set(range(0, 60, 5))),  # every 5 min
    "reconcile_signup_backlog": CronSchedule(minute={20}),  # hourly at :20
    "refresh_team_access": CronSchedule(minute=None),  # every minute
    "retention_audit": CronSchedule(minute={45}, hour={22}),
    "retention_deletion": CronSchedule(minute={30}),  # hourly at :30
    "retention_memories": CronSchedule(minute={30}, hour={21}),
    "retention_tokens": CronSchedule(minute={30}),  # hourly at :30
    "storage_stats": CronSchedule(minute={30}, hour={22}),
}


def _disabled_jobs() -> set[str]:
    """Comma-separated job names to skip, from MEM_MCP_DISABLED_JOBS env var.

    Useful for localhost dev where some jobs (e.g. reconcile_signups, which
    calls AWS Cognito ListUsers) crash with NoCredentialsError because there
    are no AWS creds. Set MEM_MCP_DISABLED_JOBS=reconcile_signups,cleanup_clients
    to skip them at scheduler boot. Whitespace is tolerated.
    """
    raw = os.environ.get("MEM_MCP_DISABLED_JOBS", "")
    return {n.strip() for n in raw.split(",") if n.strip()}


def build_core_jobs() -> list[ScheduledJob]:
    """Build ScheduledJob entries for every registered core maintenance job."""
    from mem_mcp.jobs._runner import _JOBS

    skip = _disabled_jobs()
    jobs: list[ScheduledJob] = []
    for name in sorted(_JOBS):
        if name in skip:
            log.info("core_job_disabled", extra={"job": name, "reason": "env_skip"})
            continue
        schedule = _CORE_SCHEDULES.get(name)
        if schedule is None:
            log.warning("core_job_unscheduled", extra={"job": name})
            continue
        jobs.append(
            ScheduledJob(
                name=name,
                schedule=schedule,
                argv=[sys.executable, "-m", "mem_mcp.jobs", name],
            )
        )
    return jobs


def build_plugin_jobs() -> list[ScheduledJob]:
    """Discover plugin-declared jobs and build ScheduledJob entries.

    Best-effort: a plugin that fails to load or declares an unparseable
    schedule is skipped (logged), never fatal — same posture as startup.
    """
    jobs: list[ScheduledJob] = []
    try:
        from mem_mcp.plugins.registry import PluginRegistry
    except Exception:  # pragma: no cover - import guard
        log.exception("plugin_registry_import_failed")
        return jobs

    class _Recorder:
        def __init__(self) -> None:
            self.jobs: dict[str, str] = {}

        def register(self, name: str, schedule: str, handler: object) -> None:
            self.jobs[name] = schedule

    registry = PluginRegistry()
    try:
        registry.discover()
    except Exception:
        log.exception("plugin_discovery_failed")
        return jobs

    for plugin in registry.all():
        recorder = _Recorder()
        try:
            plugin.register_jobs(recorder)
        except Exception:
            log.exception("plugin_register_jobs_failed", extra={"plugin_id": plugin.id})
            continue
        for job_name, schedule_str in recorder.jobs.items():
            try:
                schedule = parse_plugin_schedule(schedule_str)
            except ValueError:
                log.exception(
                    "plugin_schedule_parse_failed",
                    extra={"plugin_id": plugin.id, "job": job_name, "schedule": schedule_str},
                )
                continue
            jobs.append(
                ScheduledJob(
                    name=f"{plugin.id}:{job_name}",
                    schedule=schedule,
                    argv=[
                        sys.executable,
                        "-m",
                        "mem_mcp.jobs.plugin_job",
                        plugin.id,
                        job_name,
                    ],
                )
            )
    return jobs


# ---------------------------------------------------------------------------
# Run loop
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


async def _run_job(job: ScheduledJob) -> None:
    log.info("job_starting", extra={"job": job.name})
    try:
        proc = await asyncio.create_subprocess_exec(*job.argv)
        rc = await proc.wait()
    except Exception:
        log.exception("job_spawn_failed", extra={"job": job.name})
        return
    if rc == 0:
        log.info("job_completed", extra={"job": job.name})
    else:
        log.error("job_failed", extra={"job": job.name, "returncode": rc})


async def run_forever(jobs: Iterable[ScheduledJob] | None = None) -> int:
    """Schedule and run jobs forever. Returns only on cancellation."""
    catalog = list(jobs) if jobs is not None else [*build_core_jobs(), *build_plugin_jobs()]
    if not catalog:
        log.warning("scheduler_no_jobs")
    start = _now()
    for job in catalog:
        job.next_run = job.schedule.next_after(start)
        log.info(
            "job_scheduled",
            extra={"job": job.name, "next_run": job.next_run.isoformat()},
        )

    while True:
        now = _now()
        # Fire everything due, sequentially (jobs are short; minute granularity).
        for job in catalog:
            if job.next_run <= now:
                await _run_job(job)
                job.next_run = job.schedule.next_after(_now())
        # Sleep until the soonest next_run, capped so a long sleep can't mask
        # clock drift; never busy-loop below 1s.
        soonest = min((job.next_run for job in catalog), default=now + timedelta(seconds=60))
        sleep_s = max(1.0, min(60.0, (soonest - _now()).total_seconds()))
        await asyncio.sleep(sleep_s)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        return asyncio.run(run_forever())
    except KeyboardInterrupt:  # pragma: no cover
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
