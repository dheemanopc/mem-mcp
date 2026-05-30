"""memsys_create_team tool — create a new team."""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.teams.assignments import assign_role


class CreateTeamInput(BaseModel):
    """Input for memsys_create_team."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    parent_team_id: UUID | None = None
    role_in_parent: str = Field(default="member", min_length=1, max_length=64)


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

        # tenant_tx — NOT system_tx — so app.current_tenant_id is SET LOCAL.
        # The teams_insert RLS policy requires it:
        #   WITH CHECK (created_by_tenant_id = current_setting(...)::uuid)
        # Under system_tx the GUC is empty string; `''::uuid` crashes with
        # InvalidTextRepresentationError. See bug memsys:c1d61a0e (top-level
        # team creation blocked PMO bootstrap simulation).
        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
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

            # Validate parent_team_id if provided. Query the NEW
            # team_role_assignments table (RBAC source of truth) — the legacy
            # team_members table doesn't have rows for personal teams that
            # migration 0026 backfilled.
            if inp.parent_team_id is not None:
                parent_exists = await conn.fetchrow(
                    """
                    SELECT 1 FROM team_role_assignments
                    WHERE parent_team_id = $1
                      AND member_kind = 'user'
                      AND member_id = $2
                      AND status = 'active'
                    """,
                    inp.parent_team_id,
                    ctx.tenant_id,
                )
                if parent_exists is None:
                    raise JsonRpcError(
                        -32602,
                        "parent_team_not_accessible",
                    )

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

            # NOTE on the LEGACY team_members back-compat INSERT (removed):
            # The previous version of this tool ALSO inserted into team_members
            # ('admin' role). That table is the legacy enterprise PR 1.B path.
            # The new RBAC source-of-truth is team_role_assignments (assigned
            # below via assign_role). Personal teams created by migration 0026
            # already skipped team_members; surviving rows there are pre-Spec 1
            # only.
            #
            # Why removed: team_members_insert RLS requires the inserter to
            # already be an admin of the team. That's a chicken-and-egg for a
            # brand-new team. Rather than relax the legacy RLS, drop the legacy
            # write entirely — rbac.has_permission already UNIONs both tables
            # for backward read compat. Migration 0028 will drop team_members.

            # Add creator as owner in team_role_assignments (Spec 1 amendment #3)
            await assign_role(
                conn,
                parent_team_id=team["id"],
                member_kind="user",
                member_id=ctx.tenant_id,
                role_key="owner",
                assigned_by_user_id=ctx.tenant_id,
            )

            # If parent_team_id provided, add new team as member of parent
            if inp.parent_team_id is not None:
                await assign_role(
                    conn,
                    parent_team_id=inp.parent_team_id,
                    member_kind="team",
                    member_id=team["id"],
                    role_key=inp.role_in_parent,
                    assigned_by_user_id=ctx.tenant_id,
                )

        return CreateTeamOutput(
            team=TeamInfo(
                id=str(team["id"]),
                name=team["name"],
                workspace_domain=team["workspace_domain"],
                created_at=team["created_at"].isoformat(),
            ),
            your_role="owner",
        )
