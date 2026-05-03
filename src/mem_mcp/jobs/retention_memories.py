"""Soft-delete + hard-delete of memories per retention policy (T-7.14).

Per FR-5.6.2 and FR-5.6.4:
- Soft-delete: memories with created_at < now() - retention_days
- Hard-delete: memories with deleted_at < now() - 30 days
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


_log = get_logger("mem_mcp.jobs.retention_memories")


@dataclass(frozen=True)
class RetentionMemoriesStats:
    """Results of a retention pass."""

    soft_deleted_count: int
    hard_deleted_count: int


async def run(
    pool: asyncpg.Pool,
    *,
    dry_run: bool = False,
    now: Callable[[], datetime] | None = None,
) -> RetentionMemoriesStats:
    """
    Execute retention policy for memories.

    Step A (soft-delete): per-tenant, mark as deleted if past retention_days.
    Step B (hard-delete): across all tenants, purge soft-deleted past 30d grace.

    Args:
        pool: asyncpg pool
        dry_run: if True, COUNT only; don't UPDATE/DELETE
        now: callable returning current datetime (for testing); defaults to datetime.now()

    Returns:
        Stats with soft_deleted_count and hard_deleted_count.
    """
    if now is None:

        def _now() -> datetime:
            return datetime.now(UTC)

        now = _now

    _log.info("retention_memories_started", dry_run=dry_run)

    soft_deleted = 0
    hard_deleted = 0

    async with system_tx(pool) as conn:
        # ====================================================================
        # Step A: Soft-delete memories past retention_days
        # ====================================================================
        if dry_run:
            # COUNT query
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as cnt
                FROM memories m
                JOIN tenants t ON m.tenant_id = t.id
                WHERE m.deleted_at IS NULL
                  AND m.created_at < now() - (t.retention_days || ' days')::interval
                """
            )
            soft_deleted = row["cnt"] if row else 0
            _log.info("retention_memories_soft_delete_count", count=soft_deleted, dry_run=True)
        else:
            result = await conn.execute(
                """
                UPDATE memories m
                SET deleted_at = now()
                FROM tenants t
                WHERE m.tenant_id = t.id
                  AND m.deleted_at IS NULL
                  AND m.created_at < now() - (t.retention_days || ' days')::interval
                """
            )
            # Parse "UPDATE N" result string
            soft_deleted = int(result.split()[-1]) if result else 0
            _log.info("retention_memories_soft_deleted", count=soft_deleted)

        # ====================================================================
        # Step B: Hard-delete memories past 30d grace period
        # ====================================================================
        if dry_run:
            # COUNT query
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as cnt
                FROM memories
                WHERE deleted_at IS NOT NULL
                  AND deleted_at < now() - interval '30 days'
                """
            )
            hard_deleted = row["cnt"] if row else 0
            _log.info("retention_memories_hard_delete_count", count=hard_deleted, dry_run=True)
        else:
            result = await conn.execute(
                """
                DELETE FROM memories
                WHERE deleted_at IS NOT NULL
                  AND deleted_at < now() - interval '30 days'
                """
            )
            # Parse "DELETE N" result string
            hard_deleted = int(result.split()[-1]) if result else 0
            _log.info("retention_memories_hard_deleted", count=hard_deleted)

    _log.info(
        "retention_memories_done",
        soft_deleted=soft_deleted,
        hard_deleted=hard_deleted,
        dry_run=dry_run,
    )

    return RetentionMemoriesStats(
        soft_deleted_count=soft_deleted,
        hard_deleted_count=hard_deleted,
    )


async def main(dry_run: bool = False) -> int:
    """CLI entrypoint: return total affected (soft + hard)."""
    setup_logging(get_settings().log_level)
    settings = get_settings()

    import asyncpg

    pool = await asyncpg.create_pool(
        dsn=settings.db_maint_dsn,
        min_size=1,
        max_size=2,
        command_timeout=30,
        server_settings={"application_name": "mem-mcp-retention-memories"},
    )
    try:
        stats = await run(pool, dry_run=dry_run)
    finally:
        await pool.close()

    return stats.soft_deleted_count + stats.hard_deleted_count


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(prog="retention_memories")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
