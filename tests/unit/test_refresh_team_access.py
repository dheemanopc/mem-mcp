"""Tests for mem_mcp.jobs.refresh_team_access — reconciler."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from mem_mcp.jobs.refresh_team_access import main as refresh_main
from mem_mcp.teams.assignments import assign_role

if TYPE_CHECKING:
    from asyncpg import Pool  # type: ignore[import-untyped]


pytestmark = pytest.mark.asyncio


class TestReconcilerCatchUp:
    async def test_reconciler_writes_missing_uea_row(self, pg_pool: Pool) -> None:
        """After a team-as-member add with force_sync=False, no UEA for chained user.
        After reconciler runs, the row appears."""
        # Build state in its own txn (will commit so the reconciler sees it)
        async with pg_pool.acquire() as conn:
            tenant_a = await conn.fetchval(
                "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                f"test-{uuid4()}@example.test",
            )
            tenant_user = await conn.fetchval(
                "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                f"test-{uuid4()}@example.test",
            )
            team_a = await conn.fetchval(
                "INSERT INTO teams (name, created_by_tenant_id) VALUES ('rec-a', $1) RETURNING id",
                tenant_a,
            )
            team_b = await conn.fetchval(
                "INSERT INTO teams (name, created_by_tenant_id) VALUES ('rec-b', $1) RETURNING id",
                tenant_a,
            )
            await assign_role(
                conn,
                parent_team_id=team_b,
                member_kind="user",
                member_id=tenant_user,
                role_key="member",
                assigned_by_user_id=tenant_a,
            )
            # Default force_sync=False — UEA for (tenant_user, team_a) won't be written immediately
            await assign_role(
                conn,
                parent_team_id=team_a,
                member_kind="team",
                member_id=team_b,
                role_key="vendor",
                assigned_by_user_id=tenant_a,
            )

            # Confirm UEA not present yet
            row = await conn.fetchrow(
                "SELECT * FROM user_effective_team_access "
                "WHERE user_id = $1 AND resource_team_id = $2",
                tenant_user,
                team_a,
            )
            # Note: stage B's sync_refresh_on_user_assignment ran for the FIRST assign
            # (user in team_b → walks ancestors of team_b which then was empty).
            # The team-as-member add didn't fire sync_refresh. So (tenant_user, team_a) is missing.
            assert row is None

        try:
            # Run reconciler (it opens its own pool, separate connection)
            upserted = await refresh_main(dry_run=False)
            assert upserted >= 1  # at least the new (tenant_user, team_a) pair

            # Now the row should exist
            async with pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT effective_role_class FROM user_effective_team_access "
                    "WHERE user_id = $1 AND resource_team_id = $2",
                    tenant_user,
                    team_a,
                )
                assert row is not None
                assert row["effective_role_class"] == "external"
        finally:
            # Cleanup: delete teams (cascades to assignments + UEA)
            async with pg_pool.acquire() as conn:
                await conn.execute("DELETE FROM teams WHERE id IN ($1, $2)", team_a, team_b)
                await conn.execute(
                    "DELETE FROM tenants WHERE id IN ($1, $2)", tenant_a, tenant_user
                )


class TestReconcilerDryRun:
    async def test_dry_run_does_not_mutate(self, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            before = await conn.fetchval("SELECT count(*) FROM user_effective_team_access")
        upserted = await refresh_main(dry_run=True)
        async with pg_pool.acquire() as conn:
            after = await conn.fetchval("SELECT count(*) FROM user_effective_team_access")
        # Counts unchanged — dry-run doesn't write
        assert before == after
        # Returned count is still the count of pairs it would have upserted
        assert upserted >= 0
