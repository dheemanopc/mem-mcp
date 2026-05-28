"""Stub JobScheduler — registers jobs in-memory, does NOT fire them.

Real firing infrastructure (systemd units or in-process APScheduler) is a
later PR. For now, plugins can declare jobs and the registry holds them;
they just don't run yet. This unblocks plugin code that calls
register_jobs() without forcing the scheduler implementation now.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mem_mcp.plugins.contract import PluginContext

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegisteredJob:
    plugin_id: str
    name: str
    schedule: str
    handler: Callable[[PluginContext], Awaitable[None]]


class JobSchedulerImpl:
    """In-process stub. Use list_jobs() to inspect what plugins registered."""

    def __init__(self, plugin_id: str) -> None:
        self._plugin_id = plugin_id
        self._jobs: list[RegisteredJob] = []

    def register(
        self,
        name: str,
        schedule: str,
        handler: Callable[[PluginContext], Awaitable[None]],
    ) -> None:
        job = RegisteredJob(self._plugin_id, name, schedule, handler)
        self._jobs.append(job)
        log.info(
            "plugin_job_registered",
            extra={"plugin_id": self._plugin_id, "name": name, "schedule": schedule},
        )

    def list_jobs(self) -> list[RegisteredJob]:
        return list(self._jobs)
