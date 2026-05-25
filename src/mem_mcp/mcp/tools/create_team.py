"""memsys_create_team tool — create a new team."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from mem_mcp.db import system_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext


class CreateTeamInput(BaseModel):
    """Input for memsys_create_team."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)


class TeamInfo(BaseModel):
    """Created team info."""

    id: str
    name: str
    workspace_domain: str | None
    created_at: str


class CreateTeamOutput(BaseModel):
    """Output for memsys_create_team."""

    team: TeamInfo
    your_role: str


class CreateTeamTool(BaseTool):
    """Create a new team (workspace or personal)."""

    name: ClassVar[str] = "memsys_create_team"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memsys_create_team"]
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = CreateTeamInput
    OutputModel: ClassVar[type[BaseModel]] = CreateTeamOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, CreateTeamInput)

        async with system_tx(ctx.db_pool) as conn:
            # Get caller's identity info (must be primary)
            identity = await conn.fetchrow(
                """
                SELECT tenant_id, workspace_domain
                FROM tenant_identities
                WHERE tenant_id = $1 AND is_primary
                """,
                ctx.tenant_id,
            )

            if not identity:
                raise JsonRpcError(
                    -32603,
                    "caller has no primary identity",
                )

            caller_workspace_domain = identity["workspace_domain"]

            # Workspace user gets their domain; personal user gets None
            team_workspace_domain = caller_workspace_domain

            # Create team
            team = await conn.fetchrow(
                """
                INSERT INTO teams (name, workspace_domain, created_by_tenant_id)
                VALUES ($1, $2, $3)
                RETURNING id, name, workspace_domain, created_at
                """,
                inp.name,
                team_workspace_domain,
                ctx.tenant_id,
            )

            # Add creator as admin member
            await conn.execute(
                """
                INSERT INTO team_members (team_id, tenant_id, role, status, added_by_tenant_id)
                VALUES ($1, $2, $3, $4, $5)
                """,
                team["id"],
                ctx.tenant_id,
                "admin",
                "active",
                ctx.tenant_id,
            )

        return CreateTeamOutput(
            team=TeamInfo(
                id=str(team["id"]),
                name=team["name"],
                workspace_domain=team["workspace_domain"],
                created_at=team["created_at"].isoformat(),
            ),
            your_role="admin",
        )
