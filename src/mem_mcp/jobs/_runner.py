"""CLI entrypoint for jobs:  `python -m mem_mcp.jobs <job_name> [--dry-run]`.

Currently registers:
    cleanup_clients       — DCR client cleanup (T-4.9)
    retention_memories    — soft + hard-delete memories per retention policy (T-7.14)
    retention_tokens      — purge expired link_state + web_sessions (T-7.14)
    retention_audit       — anonymize + hard-delete audit log (T-7.15)
    retention_deletion    — finalize pending tenant deletions (T-7.14)

Future jobs will land here as separate handlers per LLD §4.12.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Coroutine
from typing import Any

from mem_mcp.jobs.cleanup_clients import main as cleanup_clients_main
from mem_mcp.jobs.retention_audit import main as retention_audit_main
from mem_mcp.jobs.retention_deletion import main as retention_deletion_main
from mem_mcp.jobs.retention_memories import main as retention_memories_main
from mem_mcp.jobs.retention_tokens import main as retention_tokens_main

_JobMain = Callable[..., Coroutine[Any, Any, int]]

_JOBS: dict[str, _JobMain] = {
    "cleanup_clients": cleanup_clients_main,
    "retention_audit": retention_audit_main,
    "retention_deletion": retention_deletion_main,
    "retention_memories": retention_memories_main,
    "retention_tokens": retention_tokens_main,
}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mem_mcp.jobs",
        description="Maintenance job runner",
    )
    parser.add_argument("job", choices=sorted(_JOBS.keys()))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    job = _JOBS[args.job]
    affected: int = asyncio.run(job(dry_run=args.dry_run))
    print(f"job={args.job} dry_run={args.dry_run} affected={affected}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
