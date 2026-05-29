"""Tests for teams.effective_access — recompute + sync-refresh on user assignments."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from mem_mcp.teams.assignments import assign_role, remove_member
from mem_mcp.teams.effective_access import (
    recompute_effective_access,
)

if TYPE_CHECKING:
    from asyncpg import Pool  # type: ignore[import-untyped]


pytestmark = pytest.mark.asyncio


class TestRecomputeDirect:
    async def test_direct_user_member(self, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('rec-direct', $1) RETURNING id",
                    tenant_id,
                )
                await assign_role(
                    conn,
                    parent_team_id=team_id,
                    member_kind="user",
                    member_id=tenant_id,
                    role_key="admin",
                    assigned_by_user_id=tenant_id,
                )
                computed = await recompute_effective_access(conn, tenant_id, team_id)
                assert computed is not None
                assert computed["effective_role_class"] == "internal"
                assert "admin" in computed["effective_perms"]
                assert "read" in computed["effective_perms"]
                assert "write" in computed["effective_perms"]
                assert computed["path_count"] == 1

    async def test_no_path_returns_none(self, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('rec-none', $1) RETURNING id",
                    tenant_id,
                )
                # No membership assigned
                computed = await recompute_effective_access(conn, tenant_id, team_id)
                assert computed is None


class TestSyncRefreshHook:
    async def test_assign_role_writes_uea_row(self, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('sr-assign', $1) RETURNING id",
                    tenant_id,
                )
                await assign_role(
                    conn,
                    parent_team_id=team_id,
                    member_kind="user",
                    member_id=tenant_id,
                    role_key="member",
                    assigned_by_user_id=tenant_id,
                )
                row = await conn.fetchrow(
                    "SELECT effective_role_class, path_count FROM user_effective_team_access "
                    "WHERE user_id = $1 AND resource_team_id = $2",
                    tenant_id,
                    team_id,
                )
                assert row is not None
                assert row["effective_role_class"] == "internal"
                assert row["path_count"] == 1

    async def test_remove_member_deletes_uea_row(self, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('sr-remove', $1) RETURNING id",
                    tenant_id,
                )
                # Add 2 owners so removal is allowed
                tenant_2 = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                await assign_role(
                    conn,
                    parent_team_id=team_id,
                    member_kind="user",
                    member_id=tenant_id,
                    role_key="owner",
                    assigned_by_user_id=tenant_id,
                )
                await assign_role(
                    conn,
                    parent_team_id=team_id,
                    member_kind="user",
                    member_id=tenant_2,
                    role_key="owner",
                    assigned_by_user_id=tenant_id,
                )

                # Both should have UEA rows now
                cnt_before = await conn.fetchval(
                    "SELECT count(*) FROM user_effective_team_access WHERE resource_team_id = $1",
                    team_id,
                )
                assert cnt_before == 2

                # Remove tenant_2
                await remove_member(
                    conn, parent_team_id=team_id, member_kind="user", member_id=tenant_2
                )

                # tenant_2's UEA row should be gone
                row = await conn.fetchrow(
                    "SELECT * FROM user_effective_team_access WHERE user_id = $1 AND resource_team_id = $2",
                    tenant_2,
                    team_id,
                )
                assert row is None

                # tenant_id's row remains
                row1 = await conn.fetchrow(
                    "SELECT * FROM user_effective_team_access WHERE user_id = $1 AND resource_team_id = $2",
                    tenant_id,
                    team_id,
                )
                assert row1 is not None
