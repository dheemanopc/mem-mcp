"""Team-scoped authorization checks.

Transport-agnostic on purpose (same rule as ``mem_mcp.admin.service``): raises
its own exception type, which the MCP tools map to JsonRpcError and the web
handlers map to HTTPException. Nothing here imports FastAPI or JSON-RPC.

Why this module exists: ``memsys_add_team_member``, ``memsys_assign_role`` and
``memsys_remove_team_member`` all ran under ``system_tx`` — which bypasses RLS —
with no check that the caller administers the target team. Their only gate was
``required_scope="memory.write"``, which every ordinary token carries. An
unrelated user who knew a team's UUID could add themselves as ``owner``, demote
the real owner, and inherit read access to the team's memories through
``user_effective_team_access``.

The tools' own descriptions documented the gap as "a placeholder" pending
Spec 5's effective-access table. That table shipped; the enforcement did not
follow it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from mem_mcp.auth.permissions import Permission

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


class TeamPermissionDeniedError(Exception):
    """Caller lacks the required permission on the target team."""

    def __init__(self, permission: Permission, team_id: UUID) -> None:
        self.permission = permission
        self.team_id = team_id
        super().__init__(f"caller lacks required permission on team {team_id}: {permission.value}")


async def require_team_permission(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    team_id: UUID,
    permission: Permission,
) -> None:
    """Raise ``TeamPermissionDeniedError`` unless the caller holds ``permission``.

    Resolution goes through ``has_permission``, which maps the caller's role in
    ``team_role_assignments`` against ``ROLE_PERMISSIONS``. A caller with no
    assignment on the team resolves to no role and is refused — which is the
    case that was previously wide open.
    """
    # Lazy import: mem_mcp.auth.rbac pulls in FastAPI + web.sessions for its
    # require_permission dependency, which callers on the MCP path do not need.
    from mem_mcp.auth.rbac import has_permission

    allowed = await has_permission(conn, tenant_id, permission, team_id=team_id)
    if not allowed:
        raise TeamPermissionDeniedError(permission, team_id)


async def require_can_manage_members(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    team_id: UUID,
) -> None:
    """Gate for add / assign-role / remove. Only ``owner`` and ``admin`` pass."""
    await require_team_permission(
        conn,
        tenant_id=tenant_id,
        team_id=team_id,
        permission=Permission.TEAM_MANAGE_MEMBERS,
    )
