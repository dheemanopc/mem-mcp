"""memsys_admin_get_user — full detail for one user, by tenant_id."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from mem_mcp.admin.errors import NotFoundError
from mem_mcp.admin.service import AdminUserDetail, get_user
from mem_mcp.auth.permissions import Permission
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._admin_base import AdminTool
from mem_mcp.mcp.tools._base import ToolContext

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

    from mem_mcp.audit.logger import AuditAction


class AdminGetUserInput(BaseModel):
    """Input for memsys_admin_get_user."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID


class AdminGetUserOutput(BaseModel):
    """Output for memsys_admin_get_user."""

    user: AdminUserDetail


class AdminGetUserTool(AdminTool):
    """Fetch one user with system roles and team memberships."""

    name: ClassVar[str] = "memsys_admin_get_user"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memsys_admin_get_user"]
    required_permission: ClassVar[Permission] = Permission.SYSTEM_MANAGE_TENANTS
    audit_action: ClassVar[AuditAction] = "admin.get_user"
    InputModel: ClassVar[type[BaseModel]] = AdminGetUserInput
    OutputModel: ClassVar[type[BaseModel]] = AdminGetUserOutput

    async def run(
        self,
        conn: asyncpg.Connection,
        ctx: ToolContext,
        inp: BaseModel,
    ) -> BaseModel:
        assert isinstance(inp, AdminGetUserInput)
        try:
            user = await get_user(conn, tenant_id=inp.tenant_id)
        except NotFoundError as e:
            raise JsonRpcError(-32602, str(e)) from e
        return AdminGetUserOutput(user=user)
