"""memsys_list_my_teams tool — list caller's teams."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext


class ListMyTeamsInput(BaseModel):
    """Input for memsys_list_my_teams (no parameters)."""

    model_config = ConfigDict(extra="forbid")


class TeamSummary(BaseModel):
    """Summary of a team the caller is a member of."""

    id: str
    name: str
    workspace_domain: str | None
    role: str
    member_count: int


class ListMyTeamsOutput(BaseModel):
    """Output for memsys_list_my_teams."""

    teams: list[TeamSummary]
    default_team_id: str | None


class ListMyTeamsTool(BaseTool):
    """List all teams the caller is a member of."""

    name: ClassVar[str] = "memsys_list_my_teams"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memsys_list_my_teams"]
    required_scope: ClassVar[str] = "memory.read"
    InputModel: ClassVar[type[BaseModel]] = ListMyTeamsInput
    OutputModel: ClassVar[type[BaseModel]] = ListMyTeamsOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, ListMyTeamsInput)

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            # Fetch every team where the caller is an active member, via the
            # canonical team_role_assignments + roles_catalog tables (Spec 1).
            # Personal teams backfilled by migration 0026 land here, NOT in
            # the legacy team_members table — querying the new table is what
            # makes them appear in the listing per owner decision memsys:5d3247db.
            #
            # member_count is the count of distinct user+team members in the
            # new table; matches roles users see in the dashboard.
            team_rows = await conn.fetch(
                """
                SELECT t.id, t.name, t.workspace_domain, t.created_at,
                       rc.role_key AS role,
                       tra.assigned_at AS added_at,
                       (
                           SELECT COUNT(*)
                           FROM team_role_assignments inner_tra
                           WHERE inner_tra.parent_team_id = t.id
                             AND inner_tra.status = 'active'
                       ) AS member_count
                FROM teams t
                INNER JOIN team_role_assignments tra ON tra.parent_team_id = t.id
                INNER JOIN roles_catalog rc ON rc.id = tra.role_id
                WHERE tra.member_kind = 'user'
                  AND tra.member_id = $1
                  AND tra.status = 'active'
                  AND t.deleted_at IS NULL
                ORDER BY tra.assigned_at DESC
                """,
                ctx.tenant_id,
            )

            # Fetch caller's default_team_id
            default_team_row = await conn.fetchrow(
                "SELECT default_team_id FROM tenant_identities WHERE tenant_id = $1 AND is_primary",
                ctx.tenant_id,
            )

        teams = [
            TeamSummary(
                id=str(r["id"]),
                name=r["name"],
                workspace_domain=r["workspace_domain"],
                role=r["role"],
                member_count=r["member_count"],
            )
            for r in team_rows
        ]

        default_team_id = None
        if default_team_row and default_team_row["default_team_id"]:
            default_team_id = str(default_team_row["default_team_id"])

        return ListMyTeamsOutput(teams=teams, default_team_id=default_team_id)
