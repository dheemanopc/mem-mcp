"""Reconciler job: full-scan + recompute user_effective_team_access rows.

Per memsys 1c47bd99 (Spec 5) + ratification amendment #24:
default-async cascade means transient stale-by-seconds for team-as-member
changes. This job is the safety net — runs every minute, walks every
(user, team) pair the graph implies, ensures user_effective_team_access
matches reality.

For v1 with small data (~100s of tenants per deployment), a full scan is
acceptable. At scale we'd add incremental drift-detection; deferred.
"""

from __future__ import annotations

import argparse
import asyncio

from mem_mcp.config import get_settings
from mem_mcp.db.tenant_tx import system_tx
from mem_mcp.logging_setup import get_logger, setup_logging
from mem_mcp.teams.effective_access import upsert_effective_access

_log = get_logger("mem_mcp.jobs.refresh_team_access")


async def main(dry_run: bool = False) -> int:
    """Reconcile every (user, team) UEA row + add missing ones + drop now-stale."""
    setup_logging(get_settings().log_level)
    settings = get_settings()

    import asyncpg  # type: ignore[import-untyped]

    pool = await asyncpg.create_pool(
        dsn=settings.db_maint_dsn_asyncpg,
        min_size=1,
        max_size=2,
        command_timeout=120,
        server_settings={"application_name": "mem-mcp-refresh-team-access"},
    )
    try:
        _log.info("refresh_team_access_started", dry_run=dry_run)

        async with system_tx(pool) as conn:
            # Build the universe of (user, target_team) pairs from the live graph.
            # Any user reachable to a team via the DAG should have a UEA row.
            # We use a recursive expansion: for each user, find every team reachable.
            rows = await conn.fetch(
                """
                WITH RECURSIVE user_team_reach AS (
                    -- Direct memberships
                    SELECT tra.member_id AS user_id, tra.parent_team_id AS team_id, 1 AS depth
                    FROM team_role_assignments tra
                    WHERE tra.member_kind = 'user' AND tra.status = 'active'
                    UNION
                    -- Via team-as-member
                    SELECT utr.user_id, tra2.parent_team_id, utr.depth + 1
                    FROM user_team_reach utr
                    JOIN team_role_assignments tra2
                      ON tra2.member_kind = 'team' AND tra2.member_id = utr.team_id
                    WHERE tra2.status = 'active' AND utr.depth < 5
                )
                SELECT DISTINCT user_id, team_id FROM user_team_reach
                """
            )
            implied_pairs = {(r["user_id"], r["team_id"]) for r in rows}

            existing_rows = await conn.fetch(
                "SELECT user_id, resource_team_id FROM user_effective_team_access"
            )
            existing_pairs = {(r["user_id"], r["resource_team_id"]) for r in existing_rows}

            # Pairs to upsert: union (covers implied that may have changed AND existing that may be stale)
            to_upsert = implied_pairs | existing_pairs

            # Pairs in existing but NOT in implied → orphan rows; DELETE
            orphans = existing_pairs - implied_pairs

            upserted = 0
            for user_id, team_id in to_upsert:
                if dry_run:
                    upserted += 1
                    continue
                await upsert_effective_access(conn, user_id, team_id)
                upserted += 1

            deleted = 0
            if not dry_run and orphans:
                # upsert_effective_access already DELETEs no-path rows, but be defensive
                for user_id, team_id in orphans:
                    await conn.execute(
                        "DELETE FROM user_effective_team_access WHERE user_id = $1 AND resource_team_id = $2",
                        user_id,
                        team_id,
                    )
                    deleted += 1

        _log.info(
            "refresh_team_access_done",
            upserted=upserted,
            orphans_deleted=deleted,
            implied_pairs_count=len(implied_pairs),
            existing_pairs_count=len(existing_pairs),
            dry_run=dry_run,
        )
        return upserted
    finally:
        await pool.close()


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(prog="refresh_team_access")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
