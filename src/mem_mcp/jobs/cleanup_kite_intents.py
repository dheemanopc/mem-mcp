"""Nightly cleanup of stale kite_intents rows.

kite_intents are single-use, time-bound credential handoff tokens (Option B).
Once resolved (confirmed / cancelled / smoke_failed) or expired without
resolution, there's no operational reason to retain them beyond a short
audit window. The audit_log holds the durable trail.

Cleanup rules:
  - Resolved (consumed_at IS NOT NULL) AND consumed_at < now() - 7 days
  - Unresolved (consumed_at IS NULL) AND expires_at < now() - 7 days

Both ranges use a 7-day grace beyond the lifecycle event so a curious
operator can still inspect recent rows via the maint DSN. Hard delete after.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import TYPE_CHECKING

from mem_mcp.config import get_settings
from mem_mcp.db import system_tx
from mem_mcp.logging_setup import get_logger, setup_logging

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


_log = get_logger("mem_mcp.jobs.cleanup_kite_intents")

GRACE_DAYS = 7


async def run(*, pool: asyncpg.Pool, dry_run: bool = False) -> int:
    """Run one pass. Return number of rows deleted (or that would be deleted in dry-run)."""
    async with system_tx(pool) as conn:
        if dry_run:
            cnt = await conn.fetchval(
                f"""
                SELECT COUNT(*) FROM kite_intents
                WHERE (consumed_at IS NOT NULL AND consumed_at < now() - interval '{GRACE_DAYS} days')
                   OR (consumed_at IS NULL AND expires_at < now() - interval '{GRACE_DAYS} days')
                """
            )
            _log.info("cleanup_kite_intents_dry_run", would_delete=cnt)
            return int(cnt or 0)

        result = await conn.execute(
            f"""
            DELETE FROM kite_intents
            WHERE (consumed_at IS NOT NULL AND consumed_at < now() - interval '{GRACE_DAYS} days')
               OR (consumed_at IS NULL AND expires_at < now() - interval '{GRACE_DAYS} days')
            """
        )
        # asyncpg returns "DELETE <n>"
        try:
            affected = int(result.split()[-1])
        except (IndexError, ValueError):
            affected = 0
        _log.info("cleanup_kite_intents_done", affected=affected)
        return affected


async def main(dry_run: bool = False) -> int:
    """Production entrypoint — wires real DB pool."""
    setup_logging(get_settings().log_level)
    settings = get_settings()

    import asyncpg

    pool = await asyncpg.create_pool(
        dsn=settings.db_maint_dsn_asyncpg,
        min_size=1,
        max_size=2,
        command_timeout=30,
        server_settings={"application_name": "mem-mcp-cleanup-kite-intents"},
    )
    try:
        affected = await run(pool=pool, dry_run=dry_run)
    finally:
        await pool.close()
    return affected


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(prog="cleanup_kite_intents")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
