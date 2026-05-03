"""Anonymize + hard-delete audit log per retention policy (T-7.15).

Per FR-5.5.4 and FR-14.3.3:
- Anonymize audit rows for tenants deleted > 90 days
- Hard-delete audit rows past 730 days
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


_log = get_logger("mem_mcp.jobs.retention_audit")


@dataclass(frozen=True)
class RetentionAuditStats:
    """Results of a retention pass."""

    anonymized_count: int
    hard_deleted_count: int


async def run(
    pool: asyncpg.Pool,
    *,
    dry_run: bool = False,
    now: Callable[[], datetime] | None = None,
) -> RetentionAuditStats:
    """
    Anonymize + hard-delete audit log rows per retention policy.

    Step A (anonymize): Find tenant_ids with audit row action='tenant.deleted'
    created_at < now() - 90 days. For those tenants, anonymize their audit rows
    by setting tenant_id=NULL and clearing details PII.

    Step B (hard-delete): DELETE all audit rows created_at < now() - 730 days.

    Args:
        pool: asyncpg pool
        dry_run: if True, COUNT only; don't UPDATE/DELETE
        now: callable returning current datetime (for testing); defaults to datetime.now()

    Returns:
        Stats with anonymized_count and hard_deleted_count.
    """
    if now is None:

        def _now() -> datetime:
            return datetime.now(UTC)

        now = _now

    _log.info("retention_audit_started", dry_run=dry_run)

    anonymized = 0
    hard_deleted = 0

    async with system_tx(pool) as conn:
        # ====================================================================
        # Step A: Find tenants deleted > 90 days ago
        # ====================================================================
        deletion_boundary = await conn.fetch(
            """
            SELECT DISTINCT tenant_id
            FROM audit_log
            WHERE action = 'tenant.deleted'
              AND created_at < now() - interval '90 days'
              AND tenant_id IS NOT NULL
            """
        )

        deleted_tenant_ids = [row["tenant_id"] for row in deletion_boundary]

        if deleted_tenant_ids:
            if dry_run:
                # COUNT query
                row = await conn.fetchrow(
                    """
                    SELECT COUNT(*) as cnt
                    FROM audit_log
                    WHERE tenant_id = ANY($1::uuid[])
                      AND tenant_id IS NOT NULL
                    """,
                    deleted_tenant_ids,
                )
                anonymized = row["cnt"] if row else 0
                _log.info(
                    "retention_audit_anonymize_count",
                    count=anonymized,
                    tenant_count=len(deleted_tenant_ids),
                    dry_run=True,
                )
            else:
                result = await conn.execute(
                    """
                    UPDATE audit_log
                    SET tenant_id = NULL,
                        details = jsonb_build_object('anonymized', true)
                    WHERE tenant_id = ANY($1::uuid[])
                      AND tenant_id IS NOT NULL
                    """,
                    deleted_tenant_ids,
                )
                # Parse "UPDATE N" result string
                anonymized = int(result.split()[-1]) if result else 0
                _log.info(
                    "retention_audit_anonymized",
                    count=anonymized,
                    tenant_count=len(deleted_tenant_ids),
                )

        # ====================================================================
        # Step B: Hard-delete audit rows past 730 days
        # ====================================================================
        if dry_run:
            # COUNT query
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as cnt
                FROM audit_log
                WHERE created_at < now() - interval '730 days'
                """
            )
            hard_deleted = row["cnt"] if row else 0
            _log.info("retention_audit_hard_delete_count", count=hard_deleted, dry_run=True)
        else:
            result = await conn.execute(
                """
                DELETE FROM audit_log
                WHERE created_at < now() - interval '730 days'
                """
            )
            # Parse "DELETE N" result string
            hard_deleted = int(result.split()[-1]) if result else 0
            _log.info("retention_audit_hard_deleted", count=hard_deleted)

    _log.info(
        "retention_audit_done",
        anonymized=anonymized,
        hard_deleted=hard_deleted,
        dry_run=dry_run,
    )

    return RetentionAuditStats(
        anonymized_count=anonymized,
        hard_deleted_count=hard_deleted,
    )


async def main(dry_run: bool = False) -> int:
    """CLI entrypoint: return total affected (anonymized + hard_deleted)."""
    setup_logging(get_settings().log_level)
    settings = get_settings()

    import asyncpg

    pool = await asyncpg.create_pool(
        dsn=settings.db_maint_dsn,
        min_size=1,
        max_size=2,
        command_timeout=30,
        server_settings={"application_name": "mem-mcp-retention-audit"},
    )
    try:
        stats = await run(pool, dry_run=dry_run)
    finally:
        await pool.close()

    return stats.anonymized_count + stats.hard_deleted_count


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(prog="retention_audit")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
