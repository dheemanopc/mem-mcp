"""Tests for teams.effective_access — cascade_on_team_member_change + force_sync."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from mem_mcp.teams.assignments import assign_role
from mem_mcp.teams.effective_access import cascade_on_team_member_change

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


pytestmark = pytest.mark.asyncio


class TestCascadeOnTeamMemberChange:
    async def test_returns_zero_when_no_affected_users(
        self, pg_pool: asyncpg.Pool[asyncpg.Record]
    ) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                team_a = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('cas-a', $1) RETURNING id",
                    tenant_id,
                )
                team_b = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('cas-b', $1) RETURNING id",
                    tenant_id,
                )
                # team_b has no members; cascade should return 0 refreshes
                refreshed = await cascade_on_team_member_change(conn, team_a, team_b)
                assert refreshed == 0

    async def test_force_sync_on_assign_team_writes_uea_for_chained_user(
        self, pg_pool: asyncpg.Pool[asyncpg.Record]
    ) -> None:
        """User in team_b; team_b assigned as member of team_a with force_sync=True;
        UEA row appears for (user, team_a)."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_a = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                tenant_user = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                team_a = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('fs-a', $1) RETURNING id",
                    tenant_a,
                )
                team_b = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('fs-b', $1) RETURNING id",
                    tenant_a,
                )
                # tenant_user is a member of team_b as 'member'
                await assign_role(
                    conn,
                    parent_team_id=team_b,
                    member_kind="user",
                    member_id=tenant_user,
                    role_key="member",
                    assigned_by_user_id=tenant_a,
                )
                # team_b becomes a member of team_a with force_sync=True
                await assign_role(
                    conn,
                    parent_team_id=team_a,
                    member_kind="team",
                    member_id=team_b,
                    role_key="vendor",
                    assigned_by_user_id=tenant_a,
                    force_sync=True,
                )

                # tenant_user should now have a UEA row for team_a
                row = await conn.fetchrow(
                    "SELECT effective_role_class FROM user_effective_team_access "
                    "WHERE user_id = $1 AND resource_team_id = $2",
                    tenant_user,
                    team_a,
                )
                assert row is not None
                assert row["effective_role_class"] == "external"  # vendor role-class

    async def test_default_async_no_immediate_uea_for_chained_user(
        self, pg_pool: asyncpg.Pool[asyncpg.Record]
    ) -> None:
        """Same setup as above, but force_sync=False (default): no immediate UEA row.
        The reconciler will eventually catch up; this test verifies the default-async
        contract — namely that absent force_sync, the row is NOT written."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_a = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                tenant_user = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                team_a = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('da-a', $1) RETURNING id",
                    tenant_a,
                )
                team_b = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('da-b', $1) RETURNING id",
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
                # Default force_sync=False
                await assign_role(
                    conn,
                    parent_team_id=team_a,
                    member_kind="team",
                    member_id=team_b,
                    role_key="vendor",
                    assigned_by_user_id=tenant_a,
                )

                # No UEA row written for (tenant_user, team_a) yet
                row = await conn.fetchrow(
                    "SELECT * FROM user_effective_team_access "
                    "WHERE user_id = $1 AND resource_team_id = $2",
                    tenant_user,
                    team_a,
                )
                assert row is None
