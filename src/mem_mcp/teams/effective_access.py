"""user_effective_team_access maintenance — recompute + sync refresh on user assignments.

Per memsys foundation 1c47bd99 (Spec 5), proposal amendment #23 (TABLE not MV),
and amendment #24 (sync-by-default for direct user changes; async cascade for
team-as-member changes ships in the LISTEN/NOTIFY worker — see stage C).

Effective access = union of all (path → role-class, perms) terminating at
resource_team, taken over all DAG paths. Role-class precedence:
internal > external > service (highest wins). Permissions are unioned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from mem_mcp.teams.dag import MAX_DAG_DEPTH

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


async def recompute_effective_access(
    conn: asyncpg.Connection,
    user_id: UUID,
    target_team_id: UUID,
) -> dict[str, Any] | None:
    """Compute (effective_role_class, effective_perms, path_count) for (user, target_team).

    Returns None if user has no path to target_team (in which case the caller
    should DELETE the corresponding user_effective_team_access row, if any).

    Uses the recursive CTE from test plan Section 5. Bounded by MAX_DAG_DEPTH.
    """
    row = await conn.fetchrow(
        """
        WITH RECURSIVE paths AS (
            -- Base: user is directly in target_team
            SELECT
                tra.parent_team_id AS team_id,
                rc.role_class,
                rc.permissions,
                1 AS depth
            FROM team_role_assignments tra
            JOIN roles_catalog rc ON rc.id = tra.role_id
            WHERE tra.parent_team_id = $1
              AND tra.member_kind = 'user'
              AND tra.member_id = $2
              AND tra.status = 'active'

            UNION

            -- Recurse: user reaches target_team via an intermediate team that's a member
            SELECT
                tra2.parent_team_id,
                rc2.role_class,
                rc2.permissions,
                p.depth + 1
            FROM paths p
            JOIN team_role_assignments tra2
              ON tra2.parent_team_id = $1
             AND tra2.member_kind = 'team'
             AND tra2.member_id = p.team_id
            JOIN roles_catalog rc2 ON rc2.id = tra2.role_id
            WHERE tra2.status = 'active' AND p.depth < $3
        ),
        -- Also walk: user is in some team T, T is transitively a member of target_team
        recursive_membership AS (
            -- Base: teams the user directly belongs to
            SELECT
                tra.parent_team_id AS team_id,
                rc.role_class,
                rc.permissions,
                1 AS depth
            FROM team_role_assignments tra
            JOIN roles_catalog rc ON rc.id = tra.role_id
            WHERE tra.member_kind = 'user'
              AND tra.member_id = $2
              AND tra.status = 'active'

            UNION

            -- Recurse: walk UP from each user-membership team to ancestors
            SELECT
                tra3.parent_team_id,
                rc3.role_class,
                rc3.permissions,
                rm.depth + 1
            FROM recursive_membership rm
            JOIN team_role_assignments tra3
              ON tra3.member_kind = 'team' AND tra3.member_id = rm.team_id
            JOIN roles_catalog rc3 ON rc3.id = tra3.role_id
            WHERE tra3.status = 'active' AND rm.depth < $3
        )
        ,
        all_paths AS (
            SELECT role_class, permissions FROM paths
            UNION ALL
            SELECT role_class, permissions FROM recursive_membership WHERE team_id = $1
        ),
        flat_perms AS (
            SELECT unnest(permissions) AS perm FROM all_paths
        )
        SELECT
            CASE
                WHEN bool_or(role_class = 'internal') THEN 'internal'
                WHEN bool_or(role_class = 'external') THEN 'external'
                ELSE 'service'
            END AS effective_role_class,
            (SELECT array_agg(DISTINCT perm) FROM flat_perms) AS effective_perms,
            COUNT(*) AS path_count
        FROM all_paths
        WHERE TRUE
        HAVING COUNT(*) > 0
        """,
        target_team_id,
        user_id,
        MAX_DAG_DEPTH,
    )

    if row is None or row["path_count"] == 0:
        return None
    return {
        "effective_role_class": row["effective_role_class"],
        "effective_perms": list(row["effective_perms"] or []),
        "path_count": int(row["path_count"]),
    }


async def upsert_effective_access(
    conn: asyncpg.Connection,
    user_id: UUID,
    target_team_id: UUID,
) -> None:
    """Recompute and UPSERT a single (user, target_team) row. If no path, DELETE the row."""
    computed = await recompute_effective_access(conn, user_id, target_team_id)
    if computed is None:
        await conn.execute(
            "DELETE FROM user_effective_team_access WHERE user_id = $1 AND resource_team_id = $2",
            user_id,
            target_team_id,
        )
        return
    await conn.execute(
        """
        INSERT INTO user_effective_team_access
            (user_id, resource_team_id, effective_role_class, effective_perms, path_count, refreshed_at)
        VALUES ($1, $2, $3, $4, $5, now())
        ON CONFLICT (user_id, resource_team_id) DO UPDATE SET
            effective_role_class = EXCLUDED.effective_role_class,
            effective_perms = EXCLUDED.effective_perms,
            path_count = EXCLUDED.path_count,
            refreshed_at = now()
        """,
        user_id,
        target_team_id,
        computed["effective_role_class"],
        computed["effective_perms"],
        computed["path_count"],
    )


async def sync_refresh_on_user_assignment(
    conn: asyncpg.Connection,
    user_id: UUID,
    parent_team_id: UUID,
) -> None:
    """Sync-refresh path: when a user is added/removed/role-changed in parent_team,
    recompute (user, parent_team) AND (user, every ancestor of parent_team).

    Bounded by MAX_DAG_DEPTH; recompute cost is O(MAX_DAG_DEPTH) per call.
    Called synchronously inside the assign_role / remove_member transactions.
    """
    from mem_mcp.teams.dag import walk_ancestors

    # Always refresh the direct (user, parent_team) pair.
    await upsert_effective_access(conn, user_id, parent_team_id)

    # Plus every ancestor of parent_team — user may now reach them via this new path
    # (or lose access if the assignment is being removed).
    ancestors = await walk_ancestors(conn, parent_team_id)
    for ancestor_team_id in ancestors:
        await upsert_effective_access(conn, user_id, ancestor_team_id)


async def cascade_on_team_member_change(
    conn: asyncpg.Connection,
    parent_team_id: UUID,
    member_team_id: UUID,
) -> int:
    """Recompute user_effective_team_access for every user transitively affected
    by adding/removing/changing a team-as-member relationship.

    Affected users = all users transitively reachable from member_team_id
        (i.e., users in member_team_id, in member_team_id's descendant teams,
         in users-of-teams-that-are-members-of-those, etc.).

    Affected target teams = parent_team_id AND all of parent_team_id's ancestors.

    For each (affected_user, affected_target) pair, run upsert_effective_access.

    Returns: count of (user, target_team) rows refreshed.

    Default behavior (per ratification amendment #24): this is the SYNCHRONOUS
    path. Async LISTEN/NOTIFY worker that calls this on a NOTIFY event is
    deferred to a future PR; for now, callers use force_sync=True to invoke
    this directly, OR the periodic reconciler job catches any drift within
    ~1 minute.
    """
    from mem_mcp.teams.dag import walk_ancestors, walk_descendants

    # All target teams: parent_team_id + its ancestors
    target_teams = {parent_team_id}
    target_teams.update(await walk_ancestors(conn, parent_team_id))

    # All affected users: any user with a direct membership in member_team_id
    # OR in any descendant team of member_team_id
    candidate_teams = {member_team_id}
    candidate_teams.update(await walk_descendants(conn, member_team_id))

    if not candidate_teams:
        return 0

    user_rows = await conn.fetch(
        """
        SELECT DISTINCT member_id AS user_id
        FROM team_role_assignments
        WHERE parent_team_id = ANY($1::uuid[])
          AND member_kind = 'user'
          AND status = 'active'
        """,
        list(candidate_teams),
    )
    affected_users = [r["user_id"] for r in user_rows]

    refreshed = 0
    for user_id in affected_users:
        for target in target_teams:
            await upsert_effective_access(conn, user_id, target)
            refreshed += 1
    return refreshed
