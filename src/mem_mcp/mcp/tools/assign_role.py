"""memsys_assign_role — change a member's role in a team."""
from __future__ import annotations

from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from mem_mcp.db import system_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.teams.assignments import assign_role
from mem_mcp.teams.roles import RoleNotFoundError


class AssignRoleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: UUID
    member_kind: Literal["user", "team"]
    member_id: UUID
    new_role_key: str = Field(..., min_length=1, max_length=64)


class AssignRoleOutput(BaseModel):
    team_id: str
    member_kind: str
    member_id: str
    role_key: str


class AssignRoleTool(BaseTool):
    name: ClassVar[str] = "memsys_assign_role"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memsys_assign_role"]
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = AssignRoleInput
    OutputModel: ClassVar[type[BaseModel]] = AssignRoleOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, AssignRoleInput)
        async with system_tx(ctx.db_pool) as conn:
            try:
                await assign_role(
                    conn,
                    parent_team_id=inp.team_id,
                    member_kind=inp.member_kind,
                    member_id=inp.member_id,
                    role_key=inp.new_role_key,
                    assigned_by_user_id=ctx.tenant_id,
                )
            except RoleNotFoundError as e:
                raise JsonRpcError(-32602, f"invalid_role: {e}") from e
        return AssignRoleOutput(
            team_id=str(inp.team_id),
            member_kind=inp.member_kind,
            member_id=str(inp.member_id),
            role_key=inp.new_role_key,
        )
