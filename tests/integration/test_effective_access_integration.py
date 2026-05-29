"""Integration test IT-10 — async cascade race + force_sync.

Per test plan e552d271 Section 2 + amendment #24. Tests the explicit
contract: default-async means stale-until-reconciler; force_sync=True
means correct in same txn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from mem_mcp.teams.assignments import assign_role

if TYPE_CHECKING:
    from asyncpg import Pool  # type: ignore[import-untyped]


pytestmark = pytest.mark.asyncio


class TestIT10_AsyncRaceAndForceSync:  # noqa: N801
    async def test_force_sync_true_removes_lag(self, pg_pool: Pool) -> None:
        """In same txn: assign team-as-member with force_sync=True → UEA written sync."""
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
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it10-fs-a', $1) RETURNING id",
                    tenant_a,
                )
                team_b = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it10-fs-b', $1) RETURNING id",
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
                await assign_role(
                    conn,
                    parent_team_id=team_a,
                    member_kind="team",
                    member_id=team_b,
                    role_key="vendor",
                    assigned_by_user_id=tenant_a,
                    force_sync=True,
                )
                row = await conn.fetchrow(
                    "SELECT effective_role_class FROM user_effective_team_access "
                    "WHERE user_id = $1 AND resource_team_id = $2",
                    tenant_user,
                    team_a,
                )
                assert row is not None  # sync wrote it
                assert row["effective_role_class"] == "external"

    async def test_force_sync_false_no_immediate_uea(self, pg_pool: Pool) -> None:
        """Default async path: UEA for chained user NOT written in same txn."""
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
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it10-da-a', $1) RETURNING id",
                    tenant_a,
                )
                team_b = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it10-da-b', $1) RETURNING id",
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
                await assign_role(
                    conn,
                    parent_team_id=team_a,
                    member_kind="team",
                    member_id=team_b,
                    role_key="vendor",
                    assigned_by_user_id=tenant_a,
                    # force_sync defaults False
                )
                row = await conn.fetchrow(
                    "SELECT * FROM user_effective_team_access "
                    "WHERE user_id = $1 AND resource_team_id = $2",
                    tenant_user,
                    team_a,
                )
                assert row is None  # default async: not written; reconciler handles
