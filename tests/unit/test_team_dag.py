"""Unit tests for teams.dag — DAG walks + cycle prevention + advisory lock."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mem_mcp.teams.dag import (
    CycleWouldFormError,
    acquire_dag_lock,
    can_add_team_as_member,
    walk_ancestors,
)

if TYPE_CHECKING:
    import asyncpg.pool  # type: ignore[import-untyped]

pytestmark = pytest.mark.asyncio


class TestAdvisoryLock:
    async def test_acquire_releases_on_commit(self, pg_pool: asyncpg.pool.Pool) -> None:
        """Lock acquired in one txn doesn't block another txn after commit."""
        async with pg_pool.acquire() as c1:
            async with c1.transaction():
                await acquire_dag_lock(c1)
            # After commit lock is released; second acquire should not block.
            async with pg_pool.acquire() as c2:
                async with c2.transaction():
                    await acquire_dag_lock(c2)


class TestWalkAncestors:
    async def test_empty_for_root(self, pg_pool: asyncpg.pool.Pool) -> None:
        """Team with no parents has zero ancestors."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_row = await conn.fetchrow("SELECT id FROM tenants LIMIT 1")
                if tenant_row is None:
                    pytest.skip("no tenants")
                team = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('test-root', $1) RETURNING id",
                    tenant_row["id"],
                )
                ancestors = await walk_ancestors(conn, team)
                assert ancestors == set()


class TestCanAddTeamAsMember:
    async def test_rejects_self_membership(self, pg_pool: asyncpg.pool.Pool) -> None:
        """Team cannot be a member of itself."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_row = await conn.fetchrow("SELECT id FROM tenants LIMIT 1")
                if tenant_row is None:
                    pytest.skip("no tenants")
                team = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('test-self', $1) RETURNING id",
                    tenant_row["id"],
                )
                await acquire_dag_lock(conn)
                with pytest.raises(CycleWouldFormError):
                    await can_add_team_as_member(conn, team, team)

    async def test_rejects_cycle_a_b_a(self, pg_pool: asyncpg.pool.Pool) -> None:
        """Adding A as member of B when B is already a member of A → CycleWouldFormError."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_row = await conn.fetchrow("SELECT id FROM tenants LIMIT 1")
                if tenant_row is None:
                    pytest.skip("no tenants")
                role_id = await conn.fetchval(
                    "SELECT id FROM roles_catalog WHERE role_key='member' AND plugin_id IS NULL"
                )
                team_a = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('A', $1) RETURNING id",
                    tenant_row["id"],
                )
                team_b = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('B', $1) RETURNING id",
                    tenant_row["id"],
                )
                # B is already a member of A.
                await conn.execute(
                    """
                    INSERT INTO team_role_assignments
                        (parent_team_id, member_kind, member_id, role_id, assigned_by_user_id)
                    VALUES ($1, 'team', $2, $3, $4)
                    """,
                    team_a,
                    team_b,
                    role_id,
                    tenant_row["id"],
                )
                await acquire_dag_lock(conn)
                with pytest.raises(CycleWouldFormError):
                    await can_add_team_as_member(conn, team_b, team_a)
