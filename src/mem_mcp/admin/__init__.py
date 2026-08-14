"""Admin operations — transport-agnostic service layer.

Everything here takes an ``asyncpg.Connection`` and returns pydantic models.
No FastAPI types, no JsonRpcError. The point is that the web admin routes and
the ``memsys_admin_*`` MCP tools call the *same* functions, so the two surfaces
cannot drift.

Authorization is NOT enforced here — callers gate first:
  - web:  ``require_permission(...)`` FastAPI dependency
  - MCP:  ``AdminTool.__call__`` (mem_mcp.mcp.tools._admin_base)

Both resolve through ``mem_mcp.auth.rbac.has_permission``.
"""

from __future__ import annotations

from mem_mcp.admin.errors import AdminError, ConflictError, NotFoundError
from mem_mcp.admin.service import (
    AdminUserDetail,
    AdminUserSummary,
    TeamMembership,
    find_users,
    get_user,
)

__all__ = [
    "AdminError",
    "AdminUserDetail",
    "AdminUserSummary",
    "ConflictError",
    "NotFoundError",
    "TeamMembership",
    "find_users",
    "get_user",
]
