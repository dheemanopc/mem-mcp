"""Daily transient memory expiry job.

Soft-deletes memories whose expires_at timestamp is in the past.
Used for agent-to-agent ephemeral state, session metadata, and other
short-lived content that should be automatically cleaned up.

Lives alongside other retention jobs in mem_mcp.jobs; runs daily via
the mem-mcp-expire-transient.timer systemd unit.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING

from mem_mcp.config import get_settings
from mem_mcp.db.tenant_tx import system_tx
from mem_mcp.logging_setup import get_logger, setup_logging

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


_log = get_logger("mem_mcp.jobs.expire_transient")


async def main(dry_run: bool = False) -> int:
    """Production entrypoint — soft-delete all expired transient memories."""
    setup_logging(get_settings().log_level)
    settings = get_settings()

    import asyncpg

    pool = await asyncpg.create_pool(
        dsn=settings.db_maint_dsn_asyncpg,
        min_size=1,
        max_size=2,
        command_timeout=60,
        server_settings={"application_name": "mem-mcp-expire-transient"},
    )
    try:
        _log.info("expire_transient_started", dry_run=dry_run)

        if dry_run:
            # Count expired but don't delete
            async with system_tx(pool) as conn:
                row = await conn.fetchrow(
                    """
                    SELECT COUNT(*) as count
                    FROM memories
                    WHERE expires_at IS NOT NULL
                      AND expires_at < NOW()
                      AND deleted_at IS NULL
                    """
                )
                total_expired = int(row["count"]) if row else 0
            _log.info("expire_transient_done", expired_count=total_expired, dry_run=True)
            return total_expired
        else:
            # Soft-delete expired memories and track per-tenant
            async with system_tx(pool) as conn:
                # Use CTE to soft-delete and return tenant_id for logging
                rows = await conn.fetch(
                    """
                    WITH expired AS (
                        UPDATE memories
                        SET deleted_at = NOW(), updated_at = NOW()
                        WHERE expires_at IS NOT NULL
                          AND expires_at < NOW()
                          AND deleted_at IS NULL
                        RETURNING id, tenant_id
                    )
                    SELECT tenant_id, COUNT(*) as count
                    FROM expired
                    GROUP BY tenant_id
                    """
                )
                per_tenant: dict[str, int] = defaultdict(int)
                total_expired = 0
                for row in rows:
                    tenant_id = str(row["tenant_id"])
                    count = int(row["count"])
                    per_tenant[tenant_id] = count
                    total_expired += count
                    _log.info(
                        "expire_transient_tenant_done",
                        tenant_id=tenant_id,
                        expired_count=count,
                    )
            _log.info(
                "expire_transient_done",
                total_expired=total_expired,
                tenants_affected=len(per_tenant),
                dry_run=False,
            )
            return total_expired
    finally:
        await pool.close()


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(prog="expire_transient")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
