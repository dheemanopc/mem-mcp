"""memsys_add_team_member — add a user or team as member of a team."""

from __future__ import annotations

from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from mem_mcp.db import system_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.teams.assignments import assign_role
from mem_mcp.teams.dag import CycleWouldFormError, MaxDepthExceededError
from mem_mcp.teams.roles import RoleNotFoundError


class AddTeamMemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: UUID
    member_kind: Literal["user", "team"]
    member_id: UUID | None = (
        None  # required when member_kind='team', or when adding existing user by id
    )
    member_email: str | None = None  # for member_kind='user' add-by-email path
    role_key: str = Field(..., min_length=1, max_length=64)
    force_sync: bool = False  # opt-in synchronous cascade for team-as-member


class AddTeamMemberOutput(BaseModel):
    team_id: str
    member_kind: str
    member_id: str
    role_key: str
    status: str  # 'active' or 'pending' (for email-based add when user not yet signed up)


class AddTeamMemberTool(BaseTool):
    name: ClassVar[str] = "memsys_add_team_member"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memsys_add_team_member"]
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = AddTeamMemberInput
    OutputModel: ClassVar[type[BaseModel]] = AddTeamMemberOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, AddTeamMemberInput)

        # XOR check: exactly one of member_id / member_email per member_kind rules.
        if inp.member_kind == "team":
            if inp.member_id is None:
                raise JsonRpcError(-32602, "member_id required when member_kind='team'")
            if inp.member_email is not None:
                raise JsonRpcError(-32602, "member_email not allowed when member_kind='team'")
        else:  # user
            if inp.member_id is None and inp.member_email is None:
                raise JsonRpcError(
                    -32602, "either member_id or member_email required for member_kind='user'"
                )
            if inp.member_id is not None and inp.member_email is not None:
                raise JsonRpcError(-32602, "provide either member_id or member_email, not both")

        async with system_tx(ctx.db_pool) as conn:
            # Resolve member_email → member_id if applicable
            resolved_member_id: UUID | None = inp.member_id
            status = "active"
            if inp.member_kind == "user" and inp.member_email is not None:
                row = await conn.fetchrow(
                    "SELECT tenant_id FROM tenant_identities WHERE LOWER(email) = LOWER($1) AND is_primary",
                    inp.member_email,
                )
                if row is None:
                    # Per amendment A1.1: no email invite flow yet. Reject cross-org adds for now.
                    raise JsonRpcError(
                        -32000,
                        "member_email does not match any existing user; "
                        "cross-org invitation flow not yet supported (per ratification 99eacba4 amendment A1.1)",
                    )
                resolved_member_id = row["tenant_id"]

            assert resolved_member_id is not None

            try:
                await assign_role(
                    conn,
                    parent_team_id=inp.team_id,
                    member_kind=inp.member_kind,
                    member_id=resolved_member_id,
                    role_key=inp.role_key,
                    assigned_by_user_id=ctx.tenant_id,
                    force_sync=inp.force_sync,
                )
            except RoleNotFoundError as e:
                raise JsonRpcError(-32602, f"invalid role_key: {e}") from e
            except CycleWouldFormError as e:
                raise JsonRpcError(-32000, f"cycle_would_form: {e}") from e
            except MaxDepthExceededError as e:
                raise JsonRpcError(-32000, f"max_depth_exceeded: {e}") from e

        return AddTeamMemberOutput(
            team_id=str(inp.team_id),
            member_kind=inp.member_kind,
            member_id=str(resolved_member_id),
            role_key=inp.role_key,
            status=status,
        )
