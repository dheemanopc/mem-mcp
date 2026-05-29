"""Unit tests for team MCP tools (create, add_member, remove_member, assign_role)."""

from __future__ import annotations

import os
from typing import Any, cast
from uuid import uuid4

import pytest
import pytest_asyncio

from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools.add_team_member import AddTeamMemberOutput, AddTeamMemberTool
from mem_mcp.mcp.tools.assign_role import AssignRoleOutput, AssignRoleTool
from mem_mcp.mcp.tools.create_team import CreateTeamOutput, CreateTeamTool
from mem_mcp.mcp.tools.remove_team_member import RemoveTeamMemberOutput, RemoveTeamMemberTool

# Skip all tests if MEM_MCP_TEST_DSN not set
pytestmark = pytest.mark.skipif(
    not os.getenv("MEM_MCP_TEST_DSN"),
    reason="MEM_MCP_TEST_DSN not set",
)


def _make_tool_context(pool: Any, tenant_id: Any) -> ToolContext:
    """Helper to create a ToolContext for testing."""
    return ToolContext(
        request_id=str(uuid4()),
        tenant_id=tenant_id,
        identity_id=uuid4(),
        client_id="test-client",
        scopes=frozenset(["memory.read", "memory.write"]),
        db_pool=pool,
    )


@pytest_asyncio.fixture
async def setup_teams(pg_pool: Any) -> Any:
    """Set up test teams and roles for team API tests."""
    pool = pg_pool
    tenant1_id = uuid4()
    tenant2_id = uuid4()
    team1_id = uuid4()
    team2_id = uuid4()

    async with pool.acquire() as conn:
        # Insert tenants
        await conn.execute(
            "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
            tenant1_id,
            "user1@example.com",
        )
        await conn.execute(
            "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
            tenant2_id,
            "user2@example.com",
        )

        # Insert primary identities
        await conn.execute(
            """INSERT INTO tenant_identities
               (tenant_id, cognito_sub, provider, email, is_primary)
               VALUES ($1, $2, $3, $4, $5)""",
            tenant1_id,
            f"sub-{tenant1_id}",
            "cognito",
            "user1@example.com",
            True,
        )
        await conn.execute(
            """INSERT INTO tenant_identities
               (tenant_id, cognito_sub, provider, email, is_primary)
               VALUES ($1, $2, $3, $4, $5)""",
            tenant2_id,
            f"sub-{tenant2_id}",
            "cognito",
            "user2@example.com",
            True,
        )

        # Create teams
        await conn.execute(
            "INSERT INTO teams (id, name, workspace_domain, created_by_tenant_id) VALUES ($1, $2, $3, $4)",
            team1_id,
            "Team 1",
            None,
            tenant1_id,
        )
        await conn.execute(
            "INSERT INTO teams (id, name, workspace_domain, created_by_tenant_id) VALUES ($1, $2, $3, $4)",
            team2_id,
            "Team 2",
            None,
            tenant2_id,
        )

        # Populate team_members for team1 with tenant1 as admin
        await conn.execute(
            """INSERT INTO team_members (team_id, tenant_id, role, status, added_by_tenant_id)
               VALUES ($1, $2, $3, $4, $5)""",
            team1_id,
            tenant1_id,
            "admin",
            "active",
            tenant1_id,
        )

    yield {
        "pool": pool,
        "tenant1_id": tenant1_id,
        "tenant2_id": tenant2_id,
        "team1_id": team1_id,
        "team2_id": team2_id,
    }

    # Cleanup
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM team_role_assignments WHERE parent_team_id = ANY($1::uuid[])",
            [team1_id, team2_id],
        )
        await conn.execute(
            "DELETE FROM team_members WHERE team_id = ANY($1::uuid[])", [team1_id, team2_id]
        )
        await conn.execute("DELETE FROM teams WHERE id = ANY($1::uuid[])", [team1_id, team2_id])
        await conn.execute(
            "DELETE FROM tenant_identities WHERE tenant_id = ANY($1::uuid[])",
            [tenant1_id, tenant2_id],
        )
        await conn.execute(
            "DELETE FROM tenants WHERE id = ANY($1::uuid[])", [tenant1_id, tenant2_id]
        )


@pytest.mark.asyncio
async def test_create_team_creator_is_owner_in_new_table(setup_teams: Any) -> None:
    """After create_team, check team_role_assignments has the creator with role_key='owner'."""
    from mem_mcp.mcp.tools.create_team import CreateTeamInput

    data = setup_teams
    pool = data["pool"]
    tenant_id = data["tenant1_id"]

    ctx = _make_tool_context(pool, tenant_id)
    tool = CreateTeamTool()

    inp = CreateTeamInput(name="New Team")
    output = cast(CreateTeamOutput, await tool(ctx, inp))

    assert output.your_role == "owner"

    # Verify in team_role_assignments
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT tra.member_id, tra.role_id, rc.role_key
               FROM team_role_assignments tra
               JOIN roles_catalog rc ON rc.id = tra.role_id
               WHERE tra.parent_team_id = $1 AND tra.member_kind = 'user'""",
            output.team.id,
        )
    assert row is not None
    assert str(row["member_id"]) == str(tenant_id)
    assert row["role_key"] == "owner"


@pytest.mark.asyncio
async def test_create_team_creator_is_admin_in_old_table(setup_teams: Any) -> None:
    """Back-compat: team_members.role still 'admin'."""
    from mem_mcp.mcp.tools.create_team import CreateTeamInput

    data = setup_teams
    pool = data["pool"]
    tenant_id = data["tenant1_id"]

    ctx = _make_tool_context(pool, tenant_id)
    tool = CreateTeamTool()

    inp = CreateTeamInput(name="New Team 2")
    output = cast(CreateTeamOutput, await tool(ctx, inp))

    # Verify in legacy team_members
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT role FROM team_members
               WHERE team_id = $1 AND tenant_id = $2""",
            output.team.id,
            tenant_id,
        )
    assert row is not None
    assert row["role"] == "admin"


@pytest.mark.asyncio
async def test_create_team_with_parent_assigns_team_as_member(setup_teams: Any) -> None:
    """create_team with parent_team_id sets the new team as member of parent."""
    data = setup_teams
    pool = data["pool"]
    tenant_id = data["tenant1_id"]
    parent_team_id = data["team1_id"]

    ctx = _make_tool_context(pool, tenant_id)
    tool = CreateTeamTool()

    from mem_mcp.mcp.tools.create_team import CreateTeamInput

    inp = CreateTeamInput(name="Sub Team", parent_team_id=parent_team_id)
    output = cast(CreateTeamOutput, await tool(ctx, inp))

    # Verify sub-team is in team_role_assignments as member of parent
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT tra.member_id, rc.role_key
               FROM team_role_assignments tra
               JOIN roles_catalog rc ON rc.id = tra.role_id
               WHERE tra.parent_team_id = $1 AND tra.member_kind = 'team'
                 AND tra.member_id = $2""",
            parent_team_id,
            output.team.id,
        )
    assert row is not None
    assert row["role_key"] == "member"  # default role_in_parent


@pytest.mark.asyncio
async def test_create_team_with_parent_role_in_parent_honored(setup_teams: Any) -> None:
    """role_in_parent='vendor' propagates to the assignment."""
    data = setup_teams
    pool = data["pool"]
    tenant_id = data["tenant1_id"]
    parent_team_id = data["team1_id"]

    ctx = _make_tool_context(pool, tenant_id)
    tool = CreateTeamTool()

    from mem_mcp.mcp.tools.create_team import CreateTeamInput

    inp = CreateTeamInput(
        name="Vendor Sub Team", parent_team_id=parent_team_id, role_in_parent="vendor"
    )
    output = cast(CreateTeamOutput, await tool(ctx, inp))

    # Verify role_key is vendor
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT rc.role_key
               FROM team_role_assignments tra
               JOIN roles_catalog rc ON rc.id = tra.role_id
               WHERE tra.parent_team_id = $1 AND tra.member_kind = 'team'
                 AND tra.member_id = $2""",
            parent_team_id,
            output.team.id,
        )
    assert row is not None
    assert row["role_key"] == "vendor"


@pytest.mark.asyncio
async def test_create_team_with_invalid_parent_rejects(setup_teams: Any) -> None:
    """parent_team_id pointing to non-member team → -32602."""
    data = setup_teams
    pool = data["pool"]
    tenant_id = data["tenant2_id"]  # tenant2 is NOT a member of team1
    parent_team_id = data["team1_id"]

    ctx = _make_tool_context(pool, tenant_id)
    tool = CreateTeamTool()

    from mem_mcp.mcp.tools.create_team import CreateTeamInput

    inp = CreateTeamInput(name="Should Fail", parent_team_id=parent_team_id)

    with pytest.raises(JsonRpcError) as exc_info:
        await tool(ctx, inp)

    assert exc_info.value.code == -32602
    assert "parent_team_not_accessible" in exc_info.value.message


@pytest.mark.asyncio
async def test_add_team_member_user_by_email_existing(setup_teams: Any) -> None:
    """email resolves to existing tenant."""
    data = setup_teams
    pool = data["pool"]
    tenant_id = data["tenant1_id"]  # caller is team1 member
    team_id = data["team1_id"]

    ctx = _make_tool_context(pool, tenant_id)
    tool = AddTeamMemberTool()

    from mem_mcp.mcp.tools.add_team_member import AddTeamMemberInput

    inp = AddTeamMemberInput(
        team_id=team_id, member_kind="user", member_email="user2@example.com", role_key="member"
    )
    output = cast(AddTeamMemberOutput, await tool(ctx, inp))

    assert output.status == "active"
    assert output.role_key == "member"

    # Verify in team_role_assignments
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT rc.role_key FROM team_role_assignments tra
               JOIN roles_catalog rc ON rc.id = tra.role_id
               WHERE tra.parent_team_id = $1 AND tra.member_kind = 'user'
                 AND tra.member_id = $2""",
            team_id,
            data["tenant2_id"],
        )
    assert row is not None
    assert row["role_key"] == "member"


@pytest.mark.asyncio
async def test_add_team_member_user_by_email_unknown_rejects(setup_teams: Any) -> None:
    """email doesn't match → -32000 cross-org-invite-not-supported."""
    data = setup_teams
    pool = data["pool"]
    tenant_id = data["tenant1_id"]
    team_id = data["team1_id"]

    ctx = _make_tool_context(pool, tenant_id)
    tool = AddTeamMemberTool()

    from mem_mcp.mcp.tools.add_team_member import AddTeamMemberInput

    inp = AddTeamMemberInput(
        team_id=team_id, member_kind="user", member_email="unknown@example.com", role_key="member"
    )

    with pytest.raises(JsonRpcError) as exc_info:
        await tool(ctx, inp)

    assert exc_info.value.code == -32000
    assert "cross-org invitation flow not yet supported" in exc_info.value.message


@pytest.mark.asyncio
async def test_add_team_member_team_cycle_rejects(setup_teams: Any) -> None:
    """adding cycle → -32000 cycle_would_form."""
    data = setup_teams
    pool = data["pool"]
    tenant_id = data["tenant1_id"]
    team1_id = data["team1_id"]
    team2_id = data["team2_id"]

    # First, add team2 as member of team1
    async with pool.acquire() as conn:
        from mem_mcp.teams.assignments import assign_role

        await assign_role(
            conn,
            parent_team_id=team1_id,
            member_kind="team",
            member_id=team2_id,
            role_key="member",
            assigned_by_user_id=tenant_id,
        )

    # Now try to add team1 as member of team2 (would form cycle)
    ctx = _make_tool_context(pool, tenant_id)
    tool = AddTeamMemberTool()

    from mem_mcp.mcp.tools.add_team_member import AddTeamMemberInput

    inp = AddTeamMemberInput(
        team_id=team2_id, member_kind="team", member_id=team1_id, role_key="member"
    )

    with pytest.raises(JsonRpcError) as exc_info:
        await tool(ctx, inp)

    assert exc_info.value.code == -32000
    assert "cycle_would_form" in exc_info.value.message


@pytest.mark.asyncio
async def test_add_team_member_invalid_role_key_rejects(setup_teams: Any) -> None:
    """bogus role_key → -32602."""
    data = setup_teams
    pool = data["pool"]
    tenant_id = data["tenant1_id"]
    team_id = data["team1_id"]
    target_user_id = data["tenant2_id"]

    ctx = _make_tool_context(pool, tenant_id)
    tool = AddTeamMemberTool()

    from mem_mcp.mcp.tools.add_team_member import AddTeamMemberInput

    inp = AddTeamMemberInput(
        team_id=team_id, member_kind="user", member_id=target_user_id, role_key="bogus_role"
    )

    with pytest.raises(JsonRpcError) as exc_info:
        await tool(ctx, inp)

    assert exc_info.value.code == -32602
    assert "invalid role_key" in exc_info.value.message


@pytest.mark.asyncio
async def test_remove_team_member_sole_owner_rejects(setup_teams: Any) -> None:
    """sole owner removal → -32000."""
    data = setup_teams
    pool = data["pool"]
    tenant_id = data["tenant1_id"]
    team_id = data["team1_id"]

    # Add tenant_id as owner of team1 (in new table)
    async with pool.acquire() as conn:
        from mem_mcp.teams.assignments import assign_role

        await assign_role(
            conn,
            parent_team_id=team_id,
            member_kind="user",
            member_id=tenant_id,
            role_key="owner",
            assigned_by_user_id=tenant_id,
        )

    # Try to remove the sole owner
    ctx = _make_tool_context(pool, tenant_id)
    tool = RemoveTeamMemberTool()

    from mem_mcp.mcp.tools.remove_team_member import RemoveTeamMemberInput

    inp = RemoveTeamMemberInput(team_id=team_id, member_kind="user", member_id=tenant_id)

    with pytest.raises(JsonRpcError) as exc_info:
        await tool(ctx, inp)

    assert exc_info.value.code == -32000
    assert "sole_owner_cannot_self_remove" in exc_info.value.message


@pytest.mark.asyncio
async def test_remove_team_member_happy_path(setup_teams: Any) -> None:
    """non-owner removed successfully."""
    data = setup_teams
    pool = data["pool"]
    tenant_id = data["tenant1_id"]
    team_id = data["team1_id"]
    target_user_id = data["tenant2_id"]

    # Add target_user as member
    async with pool.acquire() as conn:
        from mem_mcp.teams.assignments import assign_role

        await assign_role(
            conn,
            parent_team_id=team_id,
            member_kind="user",
            member_id=target_user_id,
            role_key="member",
            assigned_by_user_id=tenant_id,
        )

    ctx = _make_tool_context(pool, tenant_id)
    tool = RemoveTeamMemberTool()

    from mem_mcp.mcp.tools.remove_team_member import RemoveTeamMemberInput

    inp = RemoveTeamMemberInput(team_id=team_id, member_kind="user", member_id=target_user_id)
    output = cast(RemoveTeamMemberOutput, await tool(ctx, inp))

    assert output.removed is True

    # Verify removed from team_role_assignments
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT 1 FROM team_role_assignments
               WHERE parent_team_id = $1 AND member_kind = 'user' AND member_id = $2""",
            team_id,
            target_user_id,
        )
    assert row is None


@pytest.mark.asyncio
async def test_assign_role_updates_existing_assignment(setup_teams: Any) -> None:
    """UPSERT changes role_id."""
    data = setup_teams
    pool = data["pool"]
    tenant_id = data["tenant1_id"]
    team_id = data["team1_id"]
    target_user_id = data["tenant2_id"]

    # Add target_user as member
    async with pool.acquire() as conn:
        from mem_mcp.teams.assignments import assign_role

        await assign_role(
            conn,
            parent_team_id=team_id,
            member_kind="user",
            member_id=target_user_id,
            role_key="member",
            assigned_by_user_id=tenant_id,
        )

    # Now promote to admin
    ctx = _make_tool_context(pool, tenant_id)
    tool = AssignRoleTool()

    from mem_mcp.mcp.tools.assign_role import AssignRoleInput

    inp = AssignRoleInput(
        team_id=team_id, member_kind="user", member_id=target_user_id, new_role_key="admin"
    )
    output = cast(AssignRoleOutput, await tool(ctx, inp))

    assert output.role_key == "admin"

    # Verify role changed in team_role_assignments
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT rc.role_key FROM team_role_assignments tra
               JOIN roles_catalog rc ON rc.id = tra.role_id
               WHERE tra.parent_team_id = $1 AND tra.member_kind = 'user'
                 AND tra.member_id = $2""",
            team_id,
            target_user_id,
        )
    assert row is not None
    assert row["role_key"] == "admin"


@pytest.mark.asyncio
async def test_assign_role_invalid_role_rejects(setup_teams: Any) -> None:
    """unknown role → -32602."""
    data = setup_teams
    pool = data["pool"]
    tenant_id = data["tenant1_id"]
    team_id = data["team1_id"]
    target_user_id = data["tenant2_id"]

    ctx = _make_tool_context(pool, tenant_id)
    tool = AssignRoleTool()

    from mem_mcp.mcp.tools.assign_role import AssignRoleInput

    inp = AssignRoleInput(
        team_id=team_id, member_kind="user", member_id=target_user_id, new_role_key="nonexistent"
    )

    with pytest.raises(JsonRpcError) as exc_info:
        await tool(ctx, inp)

    assert exc_info.value.code == -32602
    assert "invalid_role" in exc_info.value.message
