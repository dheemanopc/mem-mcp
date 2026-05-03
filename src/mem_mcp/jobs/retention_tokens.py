"""Purge expired authorization tokens and sessions (T-7.14).

Per FR-14.3.2:
- DELETE from link_state WHERE expires_at < now()
- DELETE from web_sessions WHERE expires_at < now()
  OR (revoked_at IS NOT NULL AND revoked_at < now() - 7 days)
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mem_mcp.config import get_settings
from mem_mcp.db import system_tx
from mem_mcp.logging_setup import get_logger, setup_logging

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


_log = get_logger("mem_mcp.jobs.retention_tokens")


@dataclass(frozen=True)
class RetentionTokensStats:
    """Results of a retention pass."""

    link_state_purged: int
    web_sessions_purged: int


async def run(
    pool: asyncpg.Pool,
    *,
    dry_run: bool = False,
    now: Callable[[], datetime] | None = None,
) -> RetentionTokensStats:
    """
    Purge expired tokens and sessions.

    - link_state: DELETE WHERE expires_at < now()
    - web_sessions: DELETE WHERE expires_at < now()
                      OR (revoked_at IS NOT NULL AND revoked_at < now() - 7 days)

    Args:
        pool: asyncpg pool
        dry_run: if True, COUNT only; don't DELETE
        now: callable returning current datetime (for testing); defaults to datetime.now()

    Returns:
        Stats with link_state_purged and web_sessions_purged.
    """
    if now is None:

        def _now() -> datetime:
            return datetime.now(UTC)

        now = _now

    _log.info("retention_tokens_started", dry_run=dry_run)

    link_state_purged = 0
    web_sessions_purged = 0

    async with system_tx(pool) as conn:
        # ====================================================================
        # Purge expired link_state
        # ====================================================================
        if dry_run:
            # COUNT query
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as cnt
                FROM link_state
                WHERE expires_at < now()
                """
            )
            link_state_purged = row["cnt"] if row else 0
            _log.info("retention_tokens_link_state_count", count=link_state_purged, dry_run=True)
        else:
            result = await conn.execute(
                """
                DELETE FROM link_state
                WHERE expires_at < now()
                """
            )
            # Parse "DELETE N" result string
            link_state_purged = int(result.split()[-1]) if result else 0
            _log.info("retention_tokens_link_state_purged", count=link_state_purged)

        # ====================================================================
        # Purge expired or revoked web_sessions
        # ====================================================================
        if dry_run:
            # COUNT query
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as cnt
                FROM web_sessions
                WHERE expires_at < now()
                   OR (revoked_at IS NOT NULL AND revoked_at < now() - interval '7 days')
                """
            )
            web_sessions_purged = row["cnt"] if row else 0
            _log.info(
                "retention_tokens_web_sessions_count", count=web_sessions_purged, dry_run=True
            )
        else:
            result = await conn.execute(
                """
                DELETE FROM web_sessions
                WHERE expires_at < now()
                   OR (revoked_at IS NOT NULL AND revoked_at < now() - interval '7 days')
                """
            )
            # Parse "DELETE N" result string
            web_sessions_purged = int(result.split()[-1]) if result else 0
            _log.info("retention_tokens_web_sessions_purged", count=web_sessions_purged)

    _log.info(
        "retention_tokens_done",
        link_state_purged=link_state_purged,
        web_sessions_purged=web_sessions_purged,
        dry_run=dry_run,
    )

    return RetentionTokensStats(
        link_state_purged=link_state_purged,
        web_sessions_purged=web_sessions_purged,
    )


async def main(dry_run: bool = False) -> int:
    """CLI entrypoint: return total affected."""
    setup_logging(get_settings().log_level)
    settings = get_settings()

    import asyncpg

    pool = await asyncpg.create_pool(
        dsn=settings.db_maint_dsn,
        min_size=1,
        max_size=2,
        command_timeout=30,
        server_settings={"application_name": "mem-mcp-retention-tokens"},
    )
    try:
        stats = await run(pool, dry_run=dry_run)
    finally:
        await pool.close()

    return stats.link_state_purged + stats.web_sessions_purged


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(prog="retention_tokens")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
