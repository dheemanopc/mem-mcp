"""Daily storage-bytes recompute job.

For each active tenant, sums length(content) across non-deleted current
memories and writes the result back to tenants.total_storage_bytes plus
sets storage_bytes_updated_at = now().

This is raw-text bytes, not on-disk bytes — Postgres TOAST compresses
text, so actual disk usage is typically 30-50% less than reported here.
We're after a stable upper-bound number, not a storage-engine-accurate
one. Caller uses it as a quota signal and a UI indicator.

Lives alongside the other retention jobs in mem_mcp.jobs; runs daily via
the mem-mcp-storage-stats.timer systemd unit.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import TYPE_CHECKING

from mem_mcp.config import get_settings
from mem_mcp.db.tenant_tx import system_tx
from mem_mcp.logging_setup import get_logger, setup_logging

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


_log = get_logger("mem_mcp.jobs.storage_stats")


async def _recompute_for_tenant(conn: asyncpg.Connection, tenant_id: object) -> int:
    """Recompute total_storage_bytes for one tenant. Returns the byte total."""
    row = await conn.fetchrow(
        """
        SELECT COALESCE(SUM(length(content))::bigint, 0) AS bytes
        FROM memories
        WHERE tenant_id = $1
          AND deleted_at IS NULL
          AND is_current = true
        """,
        tenant_id,
    )
    total_bytes = int(row["bytes"]) if row is not None else 0
    await conn.execute(
        """
        UPDATE tenants
        SET total_storage_bytes = $1,
            storage_bytes_updated_at = now()
        WHERE id = $2
        """,
        total_bytes,
        tenant_id,
    )
    return total_bytes


async def main(dry_run: bool = False) -> int:
    """Production entrypoint — iterate every active tenant, recompute."""
    setup_logging(get_settings().log_level)
    settings = get_settings()

    import asyncpg

    pool = await asyncpg.create_pool(
        dsn=settings.db_maint_dsn,
        min_size=1,
        max_size=2,
        command_timeout=60,
        server_settings={"application_name": "mem-mcp-storage-stats"},
    )
    try:
        async with system_tx(pool) as conn:
            tenant_ids = [
                r["id"] for r in await conn.fetch("SELECT id FROM tenants WHERE status = 'active'")
            ]
        _log.info("storage_stats_started", tenants=len(tenant_ids), dry_run=dry_run)
        total_count = 0
        for tid in tenant_ids:
            try:
                async with system_tx(pool) as conn:
                    if dry_run:
                        # Compute but don't write.
                        row = await conn.fetchrow(
                            """
                            SELECT COALESCE(SUM(length(content))::bigint, 0) AS bytes
                            FROM memories
                            WHERE tenant_id = $1
                              AND deleted_at IS NULL
                              AND is_current = true
                            """,
                            tid,
                        )
                        bytes_total = int(row["bytes"]) if row else 0
                    else:
                        bytes_total = await _recompute_for_tenant(conn, tid)
                _log.info(
                    "storage_stats_tenant_done",
                    tenant_id=str(tid),
                    bytes=bytes_total,
                    dry_run=dry_run,
                )
                total_count += 1
            except Exception as exc:
                _log.warning(
                    "storage_stats_tenant_failed",
                    tenant_id=str(tid),
                    exc_type=type(exc).__name__,
                    exc_message=str(exc)[:200],
                )
        _log.info(
            "storage_stats_done",
            tenants_processed=total_count,
            dry_run=dry_run,
        )
        return total_count
    finally:
        await pool.close()


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(prog="storage_stats")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
