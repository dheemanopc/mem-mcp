"""Unit tests for teams.assignments — assign_role + remove_member with sole-owner block."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mem_mcp.teams.assignments import (
    SoleOwnerCannotSelfRemoveError,
    assign_role,
    remove_member,
)
from mem_mcp.teams.roles import RoleNotFoundError

if TYPE_CHECKING:
    import asyncpg.pool  # type: ignore[import-untyped]

pytestmark = pytest.mark.asyncio


class TestAssignRole:
    async def test_first_assignment(self, pg_pool: asyncpg.pool.Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_row = await conn.fetchrow("SELECT id FROM tenants LIMIT 1")
                if tenant_row is None:
                    pytest.skip("no tenants")
                team = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('asgn-test', $1) RETURNING id",
                    tenant_row["id"],
                )
                await assign_role(
                    conn,
                    parent_team_id=team,
                    member_kind="user",
                    member_id=tenant_row["id"],
                    role_key="member",
                    assigned_by_user_id=tenant_row["id"],
                )
                cnt = await conn.fetchval(
                    "SELECT count(*) FROM team_role_assignments WHERE parent_team_id = $1",
                    team,
                )
                assert cnt == 1

    async def test_unknown_role_raises(self, pg_pool: asyncpg.pool.Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_row = await conn.fetchrow("SELECT id FROM tenants LIMIT 1")
                if tenant_row is None:
                    pytest.skip("no tenants")
                team = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('asgn-bad-role', $1) RETURNING id",
                    tenant_row["id"],
                )
                with pytest.raises(RoleNotFoundError):
                    await assign_role(
                        conn,
                        parent_team_id=team,
                        member_kind="user",
                        member_id=tenant_row["id"],
                        role_key="nonexistent-role",
                        assigned_by_user_id=tenant_row["id"],
                    )


class TestRemoveMember:
    async def test_sole_owner_block(self, pg_pool: asyncpg.pool.Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_row = await conn.fetchrow("SELECT id FROM tenants LIMIT 1")
                if tenant_row is None:
                    pytest.skip("no tenants")
                team = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('owner-block', $1) RETURNING id",
                    tenant_row["id"],
                )
                await assign_role(
                    conn,
                    parent_team_id=team,
                    member_kind="user",
                    member_id=tenant_row["id"],
                    role_key="owner",
                    assigned_by_user_id=tenant_row["id"],
                )
                with pytest.raises(SoleOwnerCannotSelfRemoveError):
                    await remove_member(
                        conn,
                        parent_team_id=team,
                        member_kind="user",
                        member_id=tenant_row["id"],
                    )

    async def test_remove_non_owner_member(self, pg_pool: asyncpg.pool.Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_row = await conn.fetchrow("SELECT id FROM tenants LIMIT 1")
                if tenant_row is None:
                    pytest.skip("no tenants")
                team = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('rm-member', $1) RETURNING id",
                    tenant_row["id"],
                )
                await assign_role(
                    conn,
                    parent_team_id=team,
                    member_kind="user",
                    member_id=tenant_row["id"],
                    role_key="member",
                    assigned_by_user_id=tenant_row["id"],
                )
                await remove_member(
                    conn,
                    parent_team_id=team,
                    member_kind="user",
                    member_id=tenant_row["id"],
                )
                cnt = await conn.fetchval(
                    "SELECT count(*) FROM team_role_assignments WHERE parent_team_id = $1",
                    team,
                )
                assert cnt == 0
