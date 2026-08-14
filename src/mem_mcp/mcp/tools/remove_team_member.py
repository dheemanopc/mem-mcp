"""memsys_remove_team_member — remove a user or team from a team."""

from __future__ import annotations

from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from mem_mcp.db import system_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.teams.assignments import SoleOwnerCannotSelfRemoveError, remove_member
from mem_mcp.teams.authz import TeamPermissionDeniedError, require_can_manage_members


class RemoveTeamMemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: UUID
    member_kind: Literal["user", "team"]
    member_id: UUID
    force_sync: bool = False  # opt-in synchronous cascade for team-as-member


class RemoveTeamMemberOutput(BaseModel):
    team_id: str
    member_kind: str
    member_id: str
    removed: bool


class RemoveTeamMemberTool(BaseTool):
    name: ClassVar[str] = "memsys_remove_team_member"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memsys_remove_team_member"]
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = RemoveTeamMemberInput
    OutputModel: ClassVar[type[BaseModel]] = RemoveTeamMemberOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, RemoveTeamMemberInput)
        # Leaving a team you are in needs no admin rights — otherwise an
        # ordinary member is stuck until an owner acts. Narrowly scoped: only
        # a user removing their own assignment. The sole-owner guard inside
        # remove_member still applies, so the last owner cannot strand a team.
        removing_self = inp.member_kind == "user" and inp.member_id == ctx.tenant_id

        async with system_tx(ctx.db_pool) as conn:
            if not removing_self:
                try:
                    await require_can_manage_members(
                        conn, tenant_id=ctx.tenant_id, team_id=inp.team_id
                    )
                except TeamPermissionDeniedError as e:
                    raise JsonRpcError(
                        -32603, str(e), data={"missing_permission": e.permission.value}
                    ) from e

            try:
                await remove_member(
                    conn,
                    parent_team_id=inp.team_id,
                    member_kind=inp.member_kind,
                    member_id=inp.member_id,
                    force_sync=inp.force_sync,
                )
            except SoleOwnerCannotSelfRemoveError as e:
                raise JsonRpcError(-32000, f"sole_owner_cannot_self_remove: {e}") from e
        return RemoveTeamMemberOutput(
            team_id=str(inp.team_id),
            member_kind=inp.member_kind,
            member_id=str(inp.member_id),
            removed=True,
        )
