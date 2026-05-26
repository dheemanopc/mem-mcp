"""RBAC resolver: has_permission() + require_permission() FastAPI dependency."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import Cookie, HTTPException, Request

from mem_mcp.auth.permissions import ROLE_PERMISSIONS, Permission
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
    """Fetch the team role for a tenant in a specific team."""
    result = await conn.fetchval(
        """
        SELECT role FROM team_members
        WHERE tenant_id = $1 AND team_id = $2 AND status = 'active'
        """,
        tenant_id,
        team_id,
    )
    return result  # type: ignore[no-any-return]


async def has_permission(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    permission: Permission,
    *,
    team_id: UUID | None = None,
) -> bool:
    """Resolve a permission across system + team scopes.

    System perms: any of caller's system roles grant the perm.
    Team perms: requires team_id; caller's team role grants the perm.
    """
    if permission.value.startswith("system."):
        sys_roles = await get_system_roles(conn, tenant_id)
        return any(permission in ROLE_PERMISSIONS.get(r, set()) for r in sys_roles)

    if permission.value.startswith("team."):
        if team_id is None:
            return False
        team_role = await get_team_role(conn, tenant_id, team_id)
        if team_role is None:
            return False
        return permission in ROLE_PERMISSIONS.get(team_role, set())

    return False


def require_permission(
    permission: Permission,
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
                detail={"error": "forbidden", "missing_permission": permission.value},
            )
        return sess.tenant_id

    return _dep
