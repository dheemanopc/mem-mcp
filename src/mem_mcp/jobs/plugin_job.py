"""CLI entrypoint for plugin-registered scheduled jobs.

Usage:
    python -m mem_mcp.jobs.plugin_job <plugin_id> <job_name>

This complements `python -m mem_mcp.jobs <core_job>` for jobs that live in
plugins rather than core. Per-plugin-per-job systemd timer units invoke
this entrypoint; see `deploy/scripts/generate_plugin_units.py`.

Lifecycle per invocation:
  1. Discover plugins via the registry (same entry-point scan as server startup).
  2. Look up the requested plugin by id.
  3. Call its register_jobs() with a recording scheduler to capture all
     declared jobs by name.
  4. Look up the requested job; build a minimal PluginContext with the
     real pool + audit logger; pass it to the handler.

The PluginContext built here uses NIL UUID for tenant_id and stub clients
for per-tenant surfaces (notices, memories, credentials). Plugin jobs are
expected to be cross-tenant: they should iterate over tenants themselves
via `system_tx(ctx.pool)` and build per-tenant clients inline. The
reminders plugin's fire_due_reminders_job is the reference.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any
from uuid import UUID

log = logging.getLogger(__name__)

NIL_UUID = UUID("00000000-0000-0000-0000-000000000000")


class _RecordingScheduler:
    """Captures register() calls without scheduling — used to enumerate jobs."""

    def __init__(self) -> None:
        self.jobs: dict[str, Any] = {}

    def register(self, name: str, schedule: str, handler: Any) -> None:
        self.jobs[name] = (schedule, handler)


def _build_job_context(plugin_id: str, pool: Any) -> Any:
    """Build a minimal PluginContext suitable for a cross-tenant job.

    Per-tenant clients (notices, memories, credentials) are stubs that
    raise NotImplementedError on use — cross-tenant jobs MUST build their
    own per-tenant clients inline (see reminders fire_due_reminders_job).
    """
    from uuid import uuid4

    from pydantic import BaseModel

    from mem_mcp.audit.logger import NoopAuditLogger
    from mem_mcp.plugins.contract import PluginContext

    class _StubClient:
        """Raises if a job tries to use a per-tenant client in cross-tenant context."""

        def __getattr__(self, name: str) -> Any:
            raise NotImplementedError(
                f"plugin job tried to use per-tenant client method {name!r}; "
                "cross-tenant jobs must build per-tenant clients inline"
            )

    class _EmptyConfig(BaseModel):
        pass

    class _StubPermissions:
        def resolve(self, *args: Any, **kwargs: Any) -> Any:
            raise NotImplementedError("plugin job context has no permission resolver")

    return PluginContext(
        plugin_id=plugin_id,
        tenant_id=NIL_UUID,
        request_id=f"plugin-job-{uuid4()}",
        pool=pool,
        memories=_StubClient(),
        notices=_StubClient(),
        scheduler=_StubClient(),
        credentials=_StubClient(),
        config=_EmptyConfig(),
        audit=NoopAuditLogger(),
        permissions=_StubPermissions(),  # type: ignore[arg-type]
    )


async def _run(plugin_id: str, job_name: str) -> int:
    from mem_mcp.db.pool import init_pool
    from mem_mcp.plugins.registry import PluginRegistry

    registry = PluginRegistry()
    registry.discover()
    plugin = registry.get(plugin_id)
    if plugin is None:
        log.error(
            "plugin_job_plugin_not_found",
            extra={"plugin_id": plugin_id, "discovered": [p.id for p in registry.all()]},
        )
        return 2

    recorder = _RecordingScheduler()
    plugin.register_jobs(recorder)
    if job_name not in recorder.jobs:
        log.error(
            "plugin_job_job_not_found",
            extra={
                "plugin_id": plugin_id,
                "job_name": job_name,
                "available": sorted(recorder.jobs),
            },
        )
        return 3

    _, handler = recorder.jobs[job_name]
    pool = await init_pool()
    try:
        ctx = _build_job_context(plugin_id, pool)
        log.info("plugin_job_starting", extra={"plugin_id": plugin_id, "job": job_name})
        await handler(ctx)
        log.info("plugin_job_completed", extra={"plugin_id": plugin_id, "job": job_name})
        return 0
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(prog="mem_mcp.jobs.plugin_job")
    parser.add_argument("plugin_id", help="Plugin id (e.g. 'reminders').")
    parser.add_argument("job_name", help="Job name as declared in register_jobs().")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.plugin_id, args.job_name))


if __name__ == "__main__":
    sys.exit(main())
