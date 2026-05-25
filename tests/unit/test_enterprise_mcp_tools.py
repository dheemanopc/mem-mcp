"""Tests for enterprise team MCP tools (PR 1.B)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from mem_mcp.mcp.tools.create_team import CreateTeamInput, CreateTeamTool
from mem_mcp.mcp.tools.list_my_teams import ListMyTeamsTool


class TestListMyTeams:
    """Tests for memsys_list_my_teams tool."""

    async def test_list_teams_empty(self, tool_ctx_personal: Any) -> None:
        """New user has no teams."""
        tool = ListMyTeamsTool()
        output = await tool(tool_ctx_personal, None)  # type: ignore[arg-type]
        assert output.teams == []  # type: ignore[attr-defined]
        assert output.default_team_id is None  # type: ignore[attr-defined]

    async def test_list_teams_with_membership(
        self, tool_ctx_with_workspace: Any, db_session: Any
    ) -> None:
        """User can see teams they are in."""
        # Create a team first
        await db_session.execute(
            """
            INSERT INTO teams (id, name, workspace_domain, created_by_tenant_id)
            VALUES ($1, $2, $3, $4)
            """,
            uuid4(),
            "Team 1",
            "example.com",
            tool_ctx_with_workspace.tenant_id,
        )

        tool = ListMyTeamsTool()
        output = await tool(tool_ctx_with_workspace, None)  # type: ignore[arg-type]
        assert len(output.teams) == 1  # type: ignore[attr-defined]
        assert output.teams[0].name == "Team 1"  # type: ignore[attr-defined,index]
        assert output.teams[0].role == "admin"  # type: ignore[attr-defined,index]


class TestCreateTeam:
    """Tests for memsys_create_team tool."""

    async def test_create_team_workspace_auto_domain(self, tool_ctx_with_workspace: Any) -> None:
        """Workspace user's domain is auto-set."""
        tool = CreateTeamTool()
        inp = CreateTeamInput(name="Engineering")
        output = await tool(tool_ctx_with_workspace, inp)
        assert output.team.name == "Engineering"  # type: ignore[attr-defined]
        assert output.team.workspace_domain == "example.com"  # type: ignore[attr-defined]
        assert output.your_role == "admin"  # type: ignore[attr-defined]

    async def test_create_team_personal_no_domain(self, tool_ctx_personal: Any) -> None:
        """Personal user creates team with no domain."""
        tool = CreateTeamTool()
        inp = CreateTeamInput(name="My Team")
        output = await tool(tool_ctx_personal, inp)
        assert output.team.name == "My Team"  # type: ignore[attr-defined]
        assert output.team.workspace_domain is None  # type: ignore[attr-defined]
        assert output.your_role == "admin"  # type: ignore[attr-defined]
