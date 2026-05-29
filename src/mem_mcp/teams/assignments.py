"""Team role assignment helpers — add/update/remove with DAG validation.

Per amendment #2 (advisory lock not SERIALIZABLE) and Spec 1 (sole-owner-cannot-self-remove).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from uuid import UUID

from mem_mcp.teams.dag import acquire_dag_lock, can_add_team_as_member
from mem_mcp.teams.roles import get_role_by_key

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


class SoleOwnerCannotSelfRemoveError(Exception):
    """Last remaining owner cannot be removed without first assigning another owner."""


MemberKind = Literal["user", "team"]


async def assign_role(
    conn: asyncpg.Connection,
    *,
    parent_team_id: UUID,
    member_kind: MemberKind,
    member_id: UUID,
    role_key: str,
    assigned_by_user_id: UUID,
    plugin_id: str | None = None,
) -> None:
    """Add or update a role assignment.

    Acquires the DAG advisory lock when ``member_kind='team'`` and validates
    cycle/depth before insert. UPSERT semantics: existing assignment has its
    ``role_id`` updated and ``status`` refreshed to 'active'.

    Raises:
        RoleNotFoundError: if role_key not in catalog.
        CycleWouldFormError / MaxDepthExceededError: if team-as-member would
            violate DAG invariants.
    """
    role = await get_role_by_key(conn, role_key, plugin_id=plugin_id)

    if member_kind == "team":
        await acquire_dag_lock(conn)
        await can_add_team_as_member(conn, parent_team_id, member_id)

    await conn.execute(
        """
        INSERT INTO team_role_assignments
            (parent_team_id, member_kind, member_id, role_id, status, assigned_by_user_id)
        VALUES ($1, $2, $3, $4, 'active', $5)
        ON CONFLICT (parent_team_id, member_kind, member_id)
        DO UPDATE SET role_id = EXCLUDED.role_id, status = 'active'
        """,
        parent_team_id,
        member_kind,
        member_id,
        role["id"],
        assigned_by_user_id,
    )


async def remove_member(
    conn: asyncpg.Connection,
    *,
    parent_team_id: UUID,
    member_kind: MemberKind,
    member_id: UUID,
) -> None:
    """Remove a member from a team.

    Raises:
        SoleOwnerCannotSelfRemoveError: if removing the only owner of the team.
    """
    if member_kind == "user":
        owner_status = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS owner_count,
                bool_or(tra.member_id = $2 AND tra.member_kind = 'user') AS target_is_owner
            FROM team_role_assignments tra
            JOIN roles_catalog rc ON rc.id = tra.role_id
            WHERE tra.parent_team_id = $1
              AND rc.role_key = 'owner'
              AND rc.plugin_id IS NULL
              AND tra.status = 'active'
            """,
            parent_team_id,
            member_id,
        )
        if owner_status["target_is_owner"] and owner_status["owner_count"] == 1:
            raise SoleOwnerCannotSelfRemoveError(
                "sole owner cannot be removed; assign another owner first"
            )

    if member_kind == "team":
        await acquire_dag_lock(conn)

    await conn.execute(
        """
        DELETE FROM team_role_assignments
        WHERE parent_team_id = $1 AND member_kind = $2 AND member_id = $3
        """,
        parent_team_id,
        member_kind,
        member_id,
    )
