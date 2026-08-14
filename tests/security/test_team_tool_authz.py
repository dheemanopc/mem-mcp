"""Security: team-management MCP tools must refuse callers who don't administer the team.

Before this gate existed, `memsys_add_team_member`, `memsys_assign_role` and
`memsys_remove_team_member` ran under `system_tx` (which bypasses RLS) with no
caller check at all. Their only gate was `required_scope="memory.write"`, which
every ordinary token carries. The takeover below was verified to succeed
against the pre-fix code.

The realistic threat is not UUID guessing — a v4 UUID is not brute-forceable.
It is that anyone who has *ever* seen a team's id keeps it forever, so a
removed member could re-add themselves as owner. "Remove from team" was not
actually a revocation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest

from mem_mcp.auth.permissions import Permission
from mem_mcp.db import system_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools.add_team_member import AddTeamMemberInput, AddTeamMemberTool
from mem_mcp.mcp.tools.assign_role import AssignRoleInput, AssignRoleTool
from mem_mcp.mcp.tools.remove_team_member import RemoveTeamMemberInput, RemoveTeamMemberTool
from mem_mcp.teams.assignments import assign_role

pytestmark = pytest.mark.security


async def _mk_tenant(pool: Any, label: str) -> tuple[UUID, UUID]:
    tenant_id, identity_id = uuid4(), uuid4()
    email = f"{label}-{tenant_id}@team-authz.invalid"
    async with system_tx(pool) as conn:
        await conn.execute(
            "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
            tenant_id,
            email,
        )
        await conn.execute(
            """
            INSERT INTO tenant_identities
                (id, tenant_id, cognito_sub, provider, email, is_primary)
            VALUES ($1, $2, $3, 'cognito', $4, true)
            """,
            identity_id,
            tenant_id,
            f"sub-{tenant_id}",
            email,
        )
    return tenant_id, identity_id


def _ctx(pool: Any, tenant_id: UUID, identity_id: UUID) -> ToolContext:
    return ToolContext(
        request_id=str(uuid4()),
        tenant_id=tenant_id,
        identity_id=identity_id,
        client_id="team-authz",
        scopes=frozenset({"memory.read", "memory.write"}),
        db_pool=pool,
    )


class Scenario:
    """Victim owns a team. Attacker has no relationship to it."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def build(self) -> Scenario:
        self.victim_id, victim_identity = await _mk_tenant(self.pool, "victim")
        self.attacker_id, attacker_identity = await _mk_tenant(self.pool, "attacker")
        self.team_id = uuid4()
        async with system_tx(self.pool) as conn:
            await conn.execute(
                """
                INSERT INTO teams (id, name, workspace_domain, created_by_tenant_id)
                VALUES ($1, 'victim-private', NULL, $2)
                """,
                self.team_id,
                self.victim_id,
            )
            await assign_role(
                conn,
                parent_team_id=self.team_id,
                member_kind="user",
                member_id=self.victim_id,
                role_key="owner",
                assigned_by_user_id=self.victim_id,
            )
        self.victim_ctx = _ctx(self.pool, self.victim_id, victim_identity)
        self.attacker_ctx = _ctx(self.pool, self.attacker_id, attacker_identity)
        return self


@pytest.fixture
async def scenario(pg_pool: Any) -> AsyncIterator[Scenario]:
    yield await Scenario(pg_pool).build()


def _assert_denied(exc: pytest.ExceptionInfo[JsonRpcError]) -> None:
    assert exc.value.data is not None
    assert exc.value.data["missing_permission"] == Permission.TEAM_MANAGE_MEMBERS.value


class TestOutsiderCannotTakeOverATeam:
    async def test_cannot_add_self_as_owner(self, scenario: Scenario) -> None:
        """The original exploit: self-promotion to owner of someone else's team."""
        with pytest.raises(JsonRpcError) as exc:
            await AddTeamMemberTool()(
                scenario.attacker_ctx,
                AddTeamMemberInput(
                    team_id=scenario.team_id,
                    member_kind="user",
                    member_id=scenario.attacker_id,
                    role_key="owner",
                ),
            )
        _assert_denied(exc)

    async def test_cannot_demote_the_real_owner(self, scenario: Scenario) -> None:
        with pytest.raises(JsonRpcError) as exc:
            await AssignRoleTool()(
                scenario.attacker_ctx,
                AssignRoleInput(
                    team_id=scenario.team_id,
                    member_kind="user",
                    member_id=scenario.victim_id,
                    new_role_key="viewer",
                ),
            )
        _assert_denied(exc)

    async def test_cannot_evict_the_real_owner(self, scenario: Scenario) -> None:
        with pytest.raises(JsonRpcError) as exc:
            await RemoveTeamMemberTool()(
                scenario.attacker_ctx,
                RemoveTeamMemberInput(
                    team_id=scenario.team_id,
                    member_kind="user",
                    member_id=scenario.victim_id,
                ),
            )
        _assert_denied(exc)

    async def test_team_state_is_untouched_after_all_attempts(
        self, scenario: Scenario, pg_pool: Any
    ) -> None:
        """Belt and braces: assert the DB, not just the raised errors."""
        with pytest.raises(JsonRpcError):
            await AddTeamMemberTool()(
                scenario.attacker_ctx,
                AddTeamMemberInput(
                    team_id=scenario.team_id,
                    member_kind="user",
                    member_id=scenario.attacker_id,
                    role_key="owner",
                ),
            )
        with pytest.raises(JsonRpcError):
            await AssignRoleTool()(
                scenario.attacker_ctx,
                AssignRoleInput(
                    team_id=scenario.team_id,
                    member_kind="user",
                    member_id=scenario.victim_id,
                    new_role_key="viewer",
                ),
            )

        async with system_tx(pg_pool) as conn:
            rows = await conn.fetch(
                """
                SELECT tra.member_id, rc.role_key
                FROM team_role_assignments tra
                JOIN roles_catalog rc ON rc.id = tra.role_id
                WHERE tra.parent_team_id = $1 AND tra.status = 'active'
                """,
                scenario.team_id,
            )
            access = await conn.fetch(
                "SELECT user_id FROM user_effective_team_access WHERE resource_team_id = $1",
                scenario.team_id,
            )

        assert [(r["member_id"], r["role_key"]) for r in rows] == [(scenario.victim_id, "owner")]
        assert scenario.attacker_id not in {
            r["user_id"] for r in access
        }, "attacker gained effective access, which gates memory visibility"


class TestLegitimateUseStillWorks:
    """The gate must not lock out the people who should get through."""

    async def test_owner_can_add_a_member(self, scenario: Scenario) -> None:
        """`owner` is the role create_team grants; it was NOT in ROLE_PERMISSIONS."""
        out = await AddTeamMemberTool()(
            scenario.victim_ctx,
            AddTeamMemberInput(
                team_id=scenario.team_id,
                member_kind="user",
                member_id=scenario.attacker_id,
                role_key="member",
            ),
        )
        assert out.role_key == "member"  # type: ignore[attr-defined]

    async def test_plain_member_cannot_manage_others(self, scenario: Scenario) -> None:
        """Being in the team is not the same as administering it."""
        await AddTeamMemberTool()(
            scenario.victim_ctx,
            AddTeamMemberInput(
                team_id=scenario.team_id,
                member_kind="user",
                member_id=scenario.attacker_id,
                role_key="member",
            ),
        )
        with pytest.raises(JsonRpcError) as exc:
            await AssignRoleTool()(
                scenario.attacker_ctx,
                AssignRoleInput(
                    team_id=scenario.team_id,
                    member_kind="user",
                    member_id=scenario.victim_id,
                    new_role_key="viewer",
                ),
            )
        _assert_denied(exc)

    async def test_member_can_remove_themselves(self, scenario: Scenario) -> None:
        """Leaving a team you are in must not require admin rights."""
        await AddTeamMemberTool()(
            scenario.victim_ctx,
            AddTeamMemberInput(
                team_id=scenario.team_id,
                member_kind="user",
                member_id=scenario.attacker_id,
                role_key="member",
            ),
        )
        out = await RemoveTeamMemberTool()(
            scenario.attacker_ctx,
            RemoveTeamMemberInput(
                team_id=scenario.team_id,
                member_kind="user",
                member_id=scenario.attacker_id,
            ),
        )
        assert out.removed is True  # type: ignore[attr-defined]


class TestNoUserExistenceOracle:
    async def test_denied_add_by_email_does_not_reveal_whether_user_exists(
        self, scenario: Scenario
    ) -> None:
        """Unauthorized callers get the same refusal for real and unknown emails.

        If the email were resolved before the team gate, the differing error
        would turn this tool into an account-existence oracle.
        """
        async with system_tx(scenario.pool) as conn:
            real_email = await conn.fetchval(
                "SELECT email FROM tenants WHERE id = $1", scenario.victim_id
            )

        with pytest.raises(JsonRpcError) as known:
            await AddTeamMemberTool()(
                scenario.attacker_ctx,
                AddTeamMemberInput(
                    team_id=scenario.team_id,
                    member_kind="user",
                    member_email=real_email,
                    role_key="member",
                ),
            )
        with pytest.raises(JsonRpcError) as unknown:
            await AddTeamMemberTool()(
                scenario.attacker_ctx,
                AddTeamMemberInput(
                    team_id=scenario.team_id,
                    member_kind="user",
                    member_email="definitely-not-a-user@nowhere.invalid",
                    role_key="member",
                ),
            )

        assert known.value.code == unknown.value.code
        assert known.value.data == unknown.value.data
