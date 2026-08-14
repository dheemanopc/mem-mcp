"""memsys_admin_find_user — operator lookup of users by email or name."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from mem_mcp.admin.service import AdminUserSummary, find_users
from mem_mcp.auth.permissions import Permission
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._admin_base import AdminTool
from mem_mcp.mcp.tools._base import ToolContext

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

    from mem_mcp.audit.logger import AuditAction


class AdminFindUserInput(BaseModel):
    """Input for memsys_admin_find_user."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=2, max_length=255)
    limit: int = Field(default=20, ge=1, le=100)


class AdminFindUserOutput(BaseModel):
    """Output for memsys_admin_find_user."""

    users: list[AdminUserSummary]
    count: int


class AdminFindUserTool(AdminTool):
    """Find users across all tenants by partial email or display name."""

    name: ClassVar[str] = "memsys_admin_find_user"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memsys_admin_find_user"]
    required_permission: ClassVar[Permission] = Permission.SYSTEM_MANAGE_TENANTS
    audit_action: ClassVar[AuditAction] = "admin.find_user"
    InputModel: ClassVar[type[BaseModel]] = AdminFindUserInput
    OutputModel: ClassVar[type[BaseModel]] = AdminFindUserOutput

    async def run(
        self,
        conn: asyncpg.Connection,
        ctx: ToolContext,
        inp: BaseModel,
    ) -> BaseModel:
        assert isinstance(inp, AdminFindUserInput)
        try:
            users = await find_users(conn, query=inp.query, limit=inp.limit)
        except ValueError as e:
            raise JsonRpcError(-32602, str(e)) from e
        return AdminFindUserOutput(users=users, count=len(users))
