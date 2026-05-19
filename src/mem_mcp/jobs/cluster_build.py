"""Weekly cluster-build job — recomputes per-tenant near-duplicate clusters.

Replaces all rows in memory_clusters for each tenant with a freshly-computed
set. Idempotent: re-running produces the same shape modulo embedding drift.

Per session 2026-05-19 decision: weekly cadence (clusters change slowly,
weekly captures the value without daily compute), all tenants, full corpus.
Future opt-in / opt-out can land later if scale demands.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import TYPE_CHECKING
from uuid import UUID

from mem_mcp.config import get_settings
from mem_mcp.db.tenant_tx import system_tx, tenant_tx
from mem_mcp.logging_setup import get_logger, setup_logging
from mem_mcp.memory.similarity import (
    DEFAULT_SIMILARITY_THRESHOLD,
    build_clusters,
)

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


_log = get_logger("mem_mcp.jobs.cluster_build")


async def _build_for_tenant(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    *,
    threshold: float,
    dry_run: bool,
) -> int:
    """Build clusters for one tenant, return count written (or would-write)."""
    async with tenant_tx(pool, tenant_id) as conn:
        clusters = await build_clusters(conn, tenant_id, threshold=threshold)

        if dry_run:
            return len(clusters)

        # Wipe existing clusters and rewrite atomically.
        await conn.execute("DELETE FROM memory_clusters WHERE tenant_id = $1", tenant_id)

        if not clusters:
            return 0

        # For each cluster, pick the most-recently-updated member as seed.
        # One COALESCE-style query against memories to avoid N round-trips.
        all_member_ids = [mid for c in clusters for mid in c]
        member_rows = await conn.fetch(
            """
            SELECT id, updated_at FROM memories
            WHERE tenant_id = $1 AND id = ANY($2::uuid[])
            """,
            tenant_id,
            all_member_ids,
        )
        updated_at_by_id = {r["id"]: r["updated_at"] for r in member_rows}

        for cluster in clusters:
            # Pick seed = most-recently-updated member. Tolerate any missing
            # (race with delete) — fall back to first id.
            seed_id = max(
                cluster,
                key=lambda mid: updated_at_by_id.get(mid) or __import__("datetime").datetime.min,
            )
            await conn.execute(
                """
                INSERT INTO memory_clusters
                  (tenant_id, member_ids, member_count, similarity_threshold, seed_memory_id)
                VALUES ($1, $2::uuid[], $3, $4, $5)
                """,
                tenant_id,
                cluster,
                len(cluster),
                threshold,
                seed_id,
            )
        return len(clusters)


async def main(dry_run: bool = False, threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> int:
    """Production entrypoint — iterate every tenant, rebuild clusters."""
    setup_logging(get_settings().log_level)
    settings = get_settings()

    import asyncpg

    pool = await asyncpg.create_pool(
        dsn=settings.db_maint_dsn_asyncpg,
        min_size=1,
        max_size=4,
        command_timeout=600,
        server_settings={"application_name": "mem-mcp-cluster-build"},
    )
    try:
        async with system_tx(pool) as conn:
            tenant_ids = [
                r["id"] for r in await conn.fetch("SELECT id FROM tenants WHERE status = 'active'")
            ]
        _log.info(
            "cluster_build_started",
            tenants=len(tenant_ids),
            threshold=threshold,
            dry_run=dry_run,
        )
        total_clusters = 0
        for tid in tenant_ids:
            try:
                n = await _build_for_tenant(pool, tid, threshold=threshold, dry_run=dry_run)
                total_clusters += n
                _log.info(
                    "cluster_build_tenant_done",
                    tenant_id=str(tid),
                    clusters=n,
                    dry_run=dry_run,
                )
            except Exception as exc:
                _log.warning(
                    "cluster_build_tenant_failed",
                    tenant_id=str(tid),
                    exc_type=type(exc).__name__,
                    exc_message=str(exc)[:200],
                )
        _log.info(
            "cluster_build_done",
            total_clusters=total_clusters,
            dry_run=dry_run,
        )
        return total_clusters
    finally:
        await pool.close()


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(prog="cluster_build")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--threshold", type=float, default=DEFAULT_SIMILARITY_THRESHOLD)
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run, threshold=args.threshold))
