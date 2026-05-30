"""RBAC resolver: has_permission() + require_permission() FastAPI dependency."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import Cookie, HTTPException, Request

from mem_mcp.auth.permissions import ROLE_PERMISSIONS, Permission
from mem_mcp.auth.plugin_perms import role_has_plugin_permission
from mem_mcp.db import get_pool, system_tx
from mem_mcp.web.sessions import lookup_session

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


async def get_system_roles(conn: asyncpg.Connection, tenant_id: UUID) -> set[str]:
    """Fetch all system roles granted to a tenant."""
    rows = await conn.fetch(
        "SELECT role FROM tenant_system_roles WHERE tenant_id = $1",
        tenant_id,
    )
    return {r["role"] for r in rows}


async def get_team_role(conn: asyncpg.Connection, tenant_id: UUID, team_id: UUID) -> str | None:
    """Fetch the team role for a tenant in a specific team.

    Queries `team_role_assignments` (RBAC table from teams-foundation Spec 1).
    Falls back to legacy `team_members` table for tenants whose membership
    pre-dates the teams-foundation migration (0026 backfilled the new table
    for everyone, so this fallback is mostly defensive).
    """
    result = await conn.fetchval(
        """
        SELECT rc.role_key
        FROM team_role_assignments tra
        JOIN roles_catalog rc ON rc.id = tra.role_id
        WHERE tra.parent_team_id = $2
          AND tra.member_kind = 'user'
          AND tra.member_id = $1
          AND tra.status = 'active'
        LIMIT 1
        """,
        tenant_id,
        team_id,
    )
    if result is not None:
        return result  # type: ignore[no-any-return]
    # Legacy fallback
    legacy = await conn.fetchval(
        """
        SELECT role FROM team_members
        WHERE tenant_id = $1 AND team_id = $2 AND status = 'active'
        """,
        tenant_id,
        team_id,
    )
    return legacy  # type: ignore[no-any-return]


async def has_permission(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    permission: str | Permission,
    *,
    team_id: UUID | None = None,
) -> bool:
    """Resolve a permission across system + team + plugin scopes.

    System perms (key starts with "system."): any of caller's system roles grants.
    Team perms (key starts with "team."): requires team_id; caller's team role grants.
    Plugin perms (any other namespace, e.g. "reminders.manage_own"):
        - If `team_id` is provided, check the caller's team role against the plugin's
          declared default_grants.
        - If not, check against any team the caller belongs to — plugin perms are
          treated as personal-scope by default ("manage_own" semantics).
    """
    perm_key = permission.value if isinstance(permission, Permission) else permission

    if perm_key.startswith("system."):
        sys_roles = await get_system_roles(conn, tenant_id)
        # ROLE_PERMISSIONS stores Permission enum values; compare by string key.
        return any(
            any(p.value == perm_key for p in ROLE_PERMISSIONS.get(r, set())) for r in sys_roles
        )

    if perm_key.startswith("team."):
        if team_id is None:
            return False
        team_role = await get_team_role(conn, tenant_id, team_id)
        if team_role is None:
            return False
        return any(p.value == perm_key for p in ROLE_PERMISSIONS.get(team_role, set()))

    # Plugin-declared permission. Check plugin registry first (preferred path),
    # then fall back to core's ROLE_PERMISSIONS for legacy plugin perms still
    # declared via the Permission enum.
    if team_id is not None:
        team_role = await get_team_role(conn, tenant_id, team_id)
        if team_role is None:
            return False
        if role_has_plugin_permission(team_role, perm_key):
            return True
        return any(p.value == perm_key for p in ROLE_PERMISSIONS.get(team_role, set()))

    # No team_id: caller can use the perm if ANY team they belong to grants it.
    # Query the new team_role_assignments (RBAC source of truth post-Spec 1) +
    # the legacy team_members table for back-compat.
    rows = await conn.fetch(
        """
        SELECT DISTINCT rc.role_key AS role
        FROM team_role_assignments tra
        JOIN roles_catalog rc ON rc.id = tra.role_id
        WHERE tra.member_kind = 'user'
          AND tra.member_id = $1
          AND tra.status = 'active'
        UNION
        SELECT DISTINCT role FROM team_members
        WHERE tenant_id = $1 AND status = 'active'
        """,
        tenant_id,
    )
    for r in rows:
        role = r["role"]
        if role_has_plugin_permission(role, perm_key):
            return True
        if any(p.value == perm_key for p in ROLE_PERMISSIONS.get(role, set())):
            return True
    return False


def require_permission(
    permission: str | Permission,
    *,
    team_id_param: str | None = None,
) -> Any:
    """Build a FastAPI dependency that 401/403s if the caller lacks `permission`.

    If `team_id_param` is given, that path/query param name is extracted from the
    Request and passed to has_permission(team_id=...).
    """

    async def _dep(
        request: Request,
        mem_session: str | None = Cookie(default=None),
    ) -> UUID:
        pool = get_pool()

        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session")

        team_id: UUID | None = None
        if team_id_param:
            # Try path params first, then query
            raw = request.path_params.get(team_id_param) or request.query_params.get(team_id_param)
            if raw is not None:
                try:
                    team_id = UUID(str(raw))
                except (ValueError, TypeError) as exc:
                    raise HTTPException(status_code=400, detail=f"invalid {team_id_param}") from exc

        async with system_tx(pool) as conn:
            ok = await has_permission(conn, sess.tenant_id, permission, team_id=team_id)
        if not ok:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "forbidden",
                    "missing_permission": (
                        permission.value if isinstance(permission, Permission) else permission
                    ),
                },
            )
        return sess.tenant_id

    return _dep
