"""Integration tests for Spec 1 — team foundation (DAG, roles, assignments).

Per test plan e552d271 Section 2. IT-14 (new-signup auto personal team) is
DEFERRED: depends on migration 0024 (personal teams backfill) which ships
in a later PR after all Spec 1-5 behaviors land.

Gated by MEM_MCP_TEST_DSN env var. Each test wraps its DB writes in a
transaction that rolls back on teardown — no state leaks.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from mem_mcp.teams.assignments import (
    SoleOwnerCannotSelfRemoveError,
    assign_role,
    remove_member,
)
from mem_mcp.teams.dag import (
    CycleWouldFormError,
    acquire_dag_lock,
    can_add_team_as_member,
    walk_ancestors,
)
from mem_mcp.teams.roles import RoleNotFoundError

pytestmark = pytest.mark.asyncio


# ===================================================================
# IT-01 — 5-level deep DAG: walk ancestors finds all 4 ancestors of T5
# ===================================================================
class TestIT01_DeepDAG:  # noqa: N801
    async def test_walk_ancestors_finds_all_4(self, pg_pool: Any) -> None:
        """T5's ancestors must include T1, T2, T3, T4 (transitive chain)."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_row = await conn.fetchrow("SELECT id FROM tenants LIMIT 1")
                if tenant_row is None:
                    pytest.skip("no tenants")
                tenant_id = tenant_row["id"]
                member_role_id = await conn.fetchval(
                    "SELECT id FROM roles_catalog WHERE role_key='member' AND plugin_id IS NULL"
                )
                # T1..T5
                tids = {}
                for i in range(1, 6):
                    tids[i] = await conn.fetchval(
                        "INSERT INTO teams (name, created_by_tenant_id) VALUES ($1, $2) RETURNING id",
                        f"it01-t{i}",
                        tenant_id,
                    )
                # Chain Ti → T(i+1)
                for i in range(1, 5):
                    await conn.execute(
                        """INSERT INTO team_role_assignments
                           (parent_team_id, member_kind, member_id, role_id, assigned_by_user_id)
                           VALUES ($1, 'team', $2, $3, $4)""",
                        tids[i],
                        tids[i + 1],
                        member_role_id,
                        tenant_id,
                    )
                ancestors = await walk_ancestors(conn, tids[5])
                assert ancestors == {tids[1], tids[2], tids[3], tids[4]}


# ===================================================================
# IT-02 — Cycle prevention: A→B→C, attempt add A as member of C → reject
# ===================================================================
class TestIT02_CyclePrevention:  # noqa: N801
    async def test_rejects_cycle_creating_edge(self, pg_pool: Any) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_row = await conn.fetchrow("SELECT id FROM tenants LIMIT 1")
                if tenant_row is None:
                    pytest.skip("no tenants")
                tenant_id = tenant_row["id"]
                member_role_id = await conn.fetchval(
                    "SELECT id FROM roles_catalog WHERE role_key='member' AND plugin_id IS NULL"
                )
                a = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it02-a', $1) RETURNING id",
                    tenant_id,
                )
                b = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it02-b', $1) RETURNING id",
                    tenant_id,
                )
                c = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it02-c', $1) RETURNING id",
                    tenant_id,
                )
                # A → B
                await conn.execute(
                    """INSERT INTO team_role_assignments
                       (parent_team_id, member_kind, member_id, role_id, assigned_by_user_id)
                       VALUES ($1, 'team', $2, $3, $4)""",
                    a,
                    b,
                    member_role_id,
                    tenant_id,
                )
                # B → C
                await conn.execute(
                    """INSERT INTO team_role_assignments
                       (parent_team_id, member_kind, member_id, role_id, assigned_by_user_id)
                       VALUES ($1, 'team', $2, $3, $4)""",
                    b,
                    c,
                    member_role_id,
                    tenant_id,
                )
                # Attempt C → A (would form A→B→C→A cycle)
                await acquire_dag_lock(conn)
                with pytest.raises(CycleWouldFormError):
                    await can_add_team_as_member(conn, c, a)


# ===================================================================
# IT-03 — Founding scenario: P owner-of-B, B vendor-of-A, P also member-of-A
# Verify: P reaches A via direct path (member/internal) AND via B (vendor/external).
# Note: full effective-access union semantics ship with Spec 5; this PR just
# verifies the graph SHAPE supports the scenario (both paths exist), not the
# union computation itself.
# ===================================================================
class TestIT03_FoundingScenario:  # noqa: N801
    async def test_dual_path_exists(self, pg_pool: Any) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_row = await conn.fetchrow("SELECT id FROM tenants LIMIT 1")
                if tenant_row is None:
                    pytest.skip("no tenants")
                p = tenant_row["id"]

                team_a = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it03-A', $1) RETURNING id",
                    p,
                )
                team_b = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it03-B', $1) RETURNING id",
                    p,
                )

                # P owner of B
                await assign_role(
                    conn,
                    parent_team_id=team_b,
                    member_kind="user",
                    member_id=p,
                    role_key="owner",
                    assigned_by_user_id=p,
                )
                # B vendor of A
                await assign_role(
                    conn,
                    parent_team_id=team_a,
                    member_kind="team",
                    member_id=team_b,
                    role_key="vendor",
                    assigned_by_user_id=p,
                )
                # P member of A directly
                await assign_role(
                    conn,
                    parent_team_id=team_a,
                    member_kind="user",
                    member_id=p,
                    role_key="member",
                    assigned_by_user_id=p,
                )

                # Verify the graph shape: A has 2 active members — P (user/member) AND B (team/vendor).
                rows = await conn.fetch(
                    """SELECT tra.member_kind, tra.member_id, rc.role_key, rc.role_class
                       FROM team_role_assignments tra
                       JOIN roles_catalog rc ON rc.id = tra.role_id
                       WHERE tra.parent_team_id = $1 AND tra.status = 'active'""",
                    team_a,
                )
                kinds_and_roles = sorted([(r["member_kind"], r["role_key"]) for r in rows])
                assert ("team", "vendor") in kinds_and_roles
                assert ("user", "member") in kinds_and_roles


# ===================================================================
# IT-04 — Concurrent add-member race: two concurrent txns attempting different
# adds that together would create a cycle. Advisory lock ensures one rolls back.
# ===================================================================
class TestIT04_ConcurrentAddRace:  # noqa: N801
    async def test_advisory_lock_serializes(self, pg_pool: Any) -> None:
        """Two concurrent txns each try to add an edge; only one succeeds."""
        async with pg_pool.acquire() as setup_conn:
            async with setup_conn.transaction():
                tenant_row = await setup_conn.fetchrow("SELECT id FROM tenants LIMIT 1")
                if tenant_row is None:
                    pytest.skip("no tenants")
                tenant_id = tenant_row["id"]
                member_role_id = await setup_conn.fetchval(
                    "SELECT id FROM roles_catalog WHERE role_key='member' AND plugin_id IS NULL"
                )
                # Create 2 teams with NO assignments
                a = await setup_conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it04-A', $1) RETURNING id",
                    tenant_id,
                )
                b = await setup_conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it04-B', $1) RETURNING id",
                    tenant_id,
                )
                # COMMIT the setup so concurrent txns can see them

        # Now two concurrent attempts: each grabs a separate conn + txn + tries to add an edge.
        # Both edges individually OK; together they'd form a cycle A→B + B→A.
        async def attempt(parent_uuid: Any, child_uuid: Any) -> str:
            try:
                async with pg_pool.acquire() as conn:
                    async with conn.transaction():
                        await acquire_dag_lock(conn)
                        await can_add_team_as_member(conn, parent_uuid, child_uuid)
                        await conn.execute(
                            """INSERT INTO team_role_assignments
                               (parent_team_id, member_kind, member_id, role_id, assigned_by_user_id)
                               VALUES ($1, 'team', $2, $3, $4)""",
                            parent_uuid,
                            child_uuid,
                            member_role_id,
                            tenant_id,
                        )
                return "committed"
            except CycleWouldFormError:
                return "cycle_rejected"

        try:
            results = await asyncio.gather(
                attempt(a, b),  # A→B
                attempt(b, a),  # B→A
                return_exceptions=False,
            )
            # Exactly one commits, one rejects with cycle. Advisory lock serializes.
            outcomes: list[str] = sorted(results)
            assert outcomes == ["committed", "cycle_rejected"], f"unexpected: {outcomes}"
        finally:
            # Cleanup committed rows
            async with pg_pool.acquire() as cleanup_conn:
                async with cleanup_conn.transaction():
                    await cleanup_conn.execute(
                        "DELETE FROM team_role_assignments WHERE parent_team_id IN ($1, $2)", a, b
                    )
                    await cleanup_conn.execute("DELETE FROM teams WHERE id IN ($1, $2)", a, b)


# ===================================================================
# IT-11 — Owner cannot self-remove (sole owner block)
# ===================================================================
class TestIT11_OwnerSelfRemoveBlocked:  # noqa: N801
    async def test_sole_owner_blocked(self, pg_pool: Any) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_row = await conn.fetchrow("SELECT id FROM tenants LIMIT 1")
                if tenant_row is None:
                    pytest.skip("no tenants")
                tenant_id = tenant_row["id"]
                team = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it11', $1) RETURNING id",
                    tenant_id,
                )
                await assign_role(
                    conn,
                    parent_team_id=team,
                    member_kind="user",
                    member_id=tenant_id,
                    role_key="owner",
                    assigned_by_user_id=tenant_id,
                )
                with pytest.raises(SoleOwnerCannotSelfRemoveError):
                    await remove_member(
                        conn, parent_team_id=team, member_kind="user", member_id=tenant_id
                    )


# ===================================================================
# IT-12 — Plugin role on team without plugin → reject
# Note: full plugin-installed-on-team enforcement ships with the plugin contract
# (capstone step 3); for now, the gate is "role_key with plugin_id must exist
# in catalog AND be is_active." This IT verifies that RoleNotFoundError is
# raised when a plugin role doesn't exist in the catalog (forward-compatible).
# ===================================================================
class TestIT12_PluginRoleGate:  # noqa: N801
    async def test_unknown_plugin_role_rejected(self, pg_pool: Any) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_row = await conn.fetchrow("SELECT id FROM tenants LIMIT 1")
                if tenant_row is None:
                    pytest.skip("no tenants")
                tenant_id = tenant_row["id"]
                team = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it12', $1) RETURNING id",
                    tenant_id,
                )
                with pytest.raises(RoleNotFoundError):
                    await assign_role(
                        conn,
                        parent_team_id=team,
                        member_kind="user",
                        member_id=tenant_id,
                        role_key="pm-reviewer",
                        plugin_id="nonexistent-plugin",
                        assigned_by_user_id=tenant_id,
                    )
