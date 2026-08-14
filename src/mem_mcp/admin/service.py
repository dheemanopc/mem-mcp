"""Admin service operations — user lookup.

Cross-tenant by design: an operator looking up "who is manasi" cannot know the
tenant_id up front, which is the whole reason this layer exists. Callers MUST
have gated on ``Permission.SYSTEM_MANAGE_TENANTS`` before calling in.

These queries run under ``system_tx`` (no ``app.current_tenant_id`` GUC), so
they must never rely on RLS for filtering — there is nothing to filter by.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel

from mem_mcp.admin.errors import NotFoundError

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


# Bounds on find_users(limit). A generous ceiling: the caller is already a
# system_admin, so this is about avoiding accidental full-table dumps into an
# LLM context window, not about privilege.
_LIMIT_MIN = 1
_LIMIT_MAX = 100
_LIMIT_DEFAULT = 20

# Minimum query length. One character would match most of the table and makes
# the tool useless as a lookup while being expensive to render.
_MIN_QUERY_LEN = 2


class TeamMembership(BaseModel):
    """One team the user belongs to."""

    team_id: str
    team_name: str
    role: str


class AdminUserSummary(BaseModel):
    """A user as seen by an operator searching for them."""

    tenant_id: str
    email: str
    display_name: str | None
    status: str
    workspace_domain: str | None
    provider: str | None
    default_team_id: str | None
    created_at: str


class AdminUserDetail(AdminUserSummary):
    """A single user, with roles and team memberships resolved."""

    system_roles: list[str]
    teams: list[TeamMembership]


def _escape_like(raw: str) -> str:
    """Neutralize LIKE wildcards in operator-supplied search text.

    Without this, a query of ``%`` matches every user, and ``_`` silently
    widens the match. Backslash must go first or it double-escapes the
    wildcards we add after it. Paired with ``ESCAPE '\\'`` in the SQL.
    """
    return raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _clamp_limit(limit: int | None) -> int:
    if limit is None:
        return _LIMIT_DEFAULT
    return max(_LIMIT_MIN, min(_LIMIT_MAX, limit))


def _row_to_summary(row: asyncpg.Record) -> AdminUserSummary:
    return AdminUserSummary(
        tenant_id=str(row["tenant_id"]),
        email=row["email"],
        display_name=row["display_name"],
        status=row["status"],
        workspace_domain=row["workspace_domain"],
        provider=row["provider"],
        default_team_id=(
            str(row["default_team_id"]) if row["default_team_id"] is not None else None
        ),
        created_at=_iso(row["created_at"]),
    )


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


async def find_users(
    conn: asyncpg.Connection,
    *,
    query: str,
    limit: int | None = None,
) -> list[AdminUserSummary]:
    """Search users by partial email or display name, case-insensitive.

    Ordered newest-first, which is what an operator asking "who signed up
    recently" actually wants.

    Raises:
        ValueError: query shorter than 2 characters after stripping.
    """
    cleaned = query.strip()
    if len(cleaned) < _MIN_QUERY_LEN:
        raise ValueError(f"query must be at least {_MIN_QUERY_LEN} characters")

    pattern = f"%{_escape_like(cleaned)}%"
    rows = await conn.fetch(
        """
        SELECT t.id AS tenant_id, t.email, t.display_name, t.status, t.created_at,
               ti.workspace_domain, ti.provider, ti.default_team_id
        FROM tenants t
        LEFT JOIN tenant_identities ti
               ON ti.tenant_id = t.id AND ti.is_primary
        WHERE t.email ILIKE $1 ESCAPE '\\'
           OR t.display_name ILIKE $1 ESCAPE '\\'
        ORDER BY t.created_at DESC
        LIMIT $2
        """,
        pattern,
        _clamp_limit(limit),
    )
    return [_row_to_summary(r) for r in rows]


async def get_user(conn: asyncpg.Connection, *, tenant_id: UUID) -> AdminUserDetail:
    """Fetch one user with system roles and team memberships resolved.

    Raises:
        NotFoundError: no tenant with that id.
    """
    row = await conn.fetchrow(
        """
        SELECT t.id AS tenant_id, t.email, t.display_name, t.status, t.created_at,
               ti.workspace_domain, ti.provider, ti.default_team_id
        FROM tenants t
        LEFT JOIN tenant_identities ti
               ON ti.tenant_id = t.id AND ti.is_primary
        WHERE t.id = $1
        """,
        tenant_id,
    )
    if row is None:
        raise NotFoundError(f"no user with tenant_id {tenant_id}")

    role_rows = await conn.fetch(
        "SELECT role FROM tenant_system_roles WHERE tenant_id = $1 ORDER BY role",
        tenant_id,
    )

    # team_role_assignments is the RBAC source of truth (Spec 1); the legacy
    # team_members table is deliberately not consulted here — migration 0026
    # backfilled everyone into the new table, and 0028 drops the old one.
    team_rows = await conn.fetch(
        """
        SELECT tm.id AS team_id, tm.name AS team_name, rc.role_key AS role
        FROM team_role_assignments tra
        JOIN teams tm ON tm.id = tra.parent_team_id
        JOIN roles_catalog rc ON rc.id = tra.role_id
        WHERE tra.member_kind = 'user'
          AND tra.member_id = $1
          AND tra.status = 'active'
          AND tm.deleted_at IS NULL
        ORDER BY tra.assigned_at DESC
        """,
        tenant_id,
    )

    summary = _row_to_summary(row)
    return AdminUserDetail(
        **summary.model_dump(),
        system_roles=[r["role"] for r in role_rows],
        teams=[
            TeamMembership(
                team_id=str(r["team_id"]),
                team_name=r["team_name"],
                role=r["role"],
            )
            for r in team_rows
        ],
    )
