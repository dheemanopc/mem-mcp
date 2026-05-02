"""CLI entrypoint for jobs:  `python -m mem_mcp.jobs <job_name> [--dry-run]`.

Currently registers:
    cleanup_clients   — DCR client cleanup (T-4.9)

Future jobs will land here as separate handlers per LLD §4.12.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable

from mem_mcp.jobs.cleanup_clients import main as cleanup_clients_main

_JobMain = Callable[..., Awaitable[int]]

_JOBS: dict[str, _JobMain] = {
    "cleanup_clients": cleanup_clients_main,
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
    affected = asyncio.run(job(dry_run=args.dry_run))
    print(f"job={args.job} dry_run={args.dry_run} affected={affected}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
