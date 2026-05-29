"""Generate systemd .service + .timer units for every plugin-registered job.

Two-step wiring (memmcp can't write /etc/systemd/system, root shouldn't
import the plugin venv):

  Step 1 (as memmcp, this script):
    - Discover plugins, enumerate register_jobs() declarations
    - Render one .service + one .timer per job into a staging dir
    - Print a manifest of unit basenames to stdout (one per line)

  Step 2 (as root, in deploy.sh):
    - Read manifest, `install` each unit from staging into /etc/systemd/system
    - Remove stale mem-mcp-plugin-*.{service,timer} not in manifest
    - systemctl daemon-reload + enable+start timers

Default staging dir: /tmp/mem-mcp-plugin-units (passed via --staging).

File naming: `mem-mcp-plugin-<plugin_id>-<job_name>.{service,timer}`.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("generate_plugin_units")

UNIT_PREFIX = "mem-mcp-plugin-"
POETRY_BIN = "/home/memmcp/.local/bin/poetry"
WORKING_DIR = "/opt/mem-mcp"
ENV_FILE = "/etc/mem-mcp/env"


SERVICE_TEMPLATE = """[Unit]
Description=mem-mcp plugin job {plugin_id}:{job_name}
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=oneshot
User=memmcp
Group=memmcp
WorkingDirectory={working_dir}
EnvironmentFile={env_file}
ExecStart={poetry} run python -m mem_mcp.jobs.plugin_job {plugin_id} {job_name}

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict

LimitNOFILE=4096
"""


TIMER_TEMPLATE = """[Unit]
Description=Run mem-mcp plugin job {plugin_id}:{job_name} on schedule
Requires={unit_base}.service

[Timer]
{timer_body}
[Install]
WantedBy=timers.target
"""


class _RecordingScheduler:
    def __init__(self) -> None:
        self.jobs: dict[str, str] = {}  # name -> schedule

    def register(self, name: str, schedule: str, handler: Any) -> None:
        self.jobs[name] = schedule


def _enumerate_plugin_jobs() -> list[tuple[str, str, str]]:
    """Return [(plugin_id, job_name, schedule), ...] for every plugin job."""
    from mem_mcp.plugins.registry import PluginRegistry

    registry = PluginRegistry()
    registry.discover()
    out: list[tuple[str, str, str]] = []
    for plugin in registry.all():
        recorder = _RecordingScheduler()
        try:
            plugin.register_jobs(recorder)
        except Exception:
            log.exception("plugin_register_jobs_failed", extra={"plugin_id": plugin.id})
            continue
        for name, schedule in recorder.jobs.items():
            out.append((plugin.id, name, schedule))
    return out


def _render_unit(plugin_id: str, job_name: str, schedule: str) -> tuple[str, str, str]:
    """Return (unit_base, service_text, timer_text)."""
    from mem_mcp.plugins.schedule import schedule_to_timer_body

    unit_base = f"{UNIT_PREFIX}{plugin_id}-{job_name}"
    timer_body = schedule_to_timer_body(schedule)
    service_text = SERVICE_TEMPLATE.format(
        plugin_id=plugin_id,
        job_name=job_name,
        working_dir=WORKING_DIR,
        env_file=ENV_FILE,
        poetry=POETRY_BIN,
    )
    timer_text = TIMER_TEMPLATE.format(
        plugin_id=plugin_id,
        job_name=job_name,
        unit_base=unit_base,
        timer_body=timer_body,
    )
    return unit_base, service_text, timer_text


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--staging",
        default="/tmp/mem-mcp-plugin-units",
        help="Directory to write generated unit files to (must be writable by current user).",
    )
    args = parser.parse_args()
    staging = Path(args.staging)
    staging.mkdir(parents=True, exist_ok=True)
    # Clean staging so a removed plugin job doesn't leak into the manifest.
    for stale in staging.glob(f"{UNIT_PREFIX}*"):
        stale.unlink()

    written: list[str] = []
    for plugin_id, job_name, schedule in _enumerate_plugin_jobs():
        try:
            unit_base, service_text, timer_text = _render_unit(plugin_id, job_name, schedule)
        except Exception:
            log.exception(
                "render_unit_failed",
                extra={"plugin_id": plugin_id, "job_name": job_name, "schedule": schedule},
            )
            continue
        (staging / f"{unit_base}.service").write_text(service_text)
        (staging / f"{unit_base}.timer").write_text(timer_text)
        written.append(unit_base)
        log.info("staged_unit", extra={"base": unit_base, "schedule": schedule})

    # Manifest to stdout (one unit base per line) — consumed by deploy.sh.
    for base in sorted(written):
        print(base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
