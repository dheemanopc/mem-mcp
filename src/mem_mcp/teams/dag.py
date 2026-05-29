"""DAG-walk helpers + cycle prevention + advisory lock.

Per memsys amendment #2 in 99eacba4: transaction-scoped ADVISORY LOCK
(not SERIALIZABLE isolation) for graph-mutation concurrency.

The lock key is a single global integer; only one team-graph-mutation
runs at a time across the entire pool. Lock scope = the transaction
holding it; released on COMMIT/ROLLBACK.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

# Single global advisory lock key for ALL team-graph mutations.
# Document that this serializes every team add/remove across the deployment;
# acceptable because graph mutations are rare and short-lived.
DAG_MUTATION_LOCK_KEY: int = 0x7EAB_C0DE_DA61_4001  # "TEAM CODE DAG 1"

MAX_DAG_DEPTH: int = 5


class TeamGraphError(Exception):
    """Base for team-graph errors."""


class CycleWouldFormError(TeamGraphError):
    """Adding the edge would create a cycle in the DAG."""


class MaxDepthExceededError(TeamGraphError):
    """Adding the edge would exceed MAX_DAG_DEPTH."""


class TeamNotFoundError(TeamGraphError):
    """Referenced team does not exist."""


async def acquire_dag_lock(conn: asyncpg.Connection) -> None:
    """Acquire the global team-graph advisory lock (transaction-scoped).

    Blocks until acquired. Released automatically on COMMIT/ROLLBACK.
    Caller must already be in a transaction.
    """
    await conn.execute("SELECT pg_advisory_xact_lock($1)", DAG_MUTATION_LOCK_KEY)


async def walk_ancestors(conn: asyncpg.Connection, team_id: UUID) -> set[UUID]:
    """Return the set of teams that have ``team_id`` as a descendant.

    Bounded by MAX_DAG_DEPTH. Uses recursive CTE on team_role_assignments
    WHERE member_kind='team'.
    """
    rows = await conn.fetch(
        """
        WITH RECURSIVE ancestors AS (
            SELECT parent_team_id, 1 AS depth
            FROM team_role_assignments
            WHERE member_kind = 'team' AND member_id = $1 AND status = 'active'
            UNION
            SELECT tra.parent_team_id, a.depth + 1
            FROM team_role_assignments tra
            JOIN ancestors a
              ON tra.member_kind = 'team' AND tra.member_id = a.parent_team_id
            WHERE tra.status = 'active' AND a.depth < $2
        )
        SELECT DISTINCT parent_team_id FROM ancestors
        """,
        team_id,
        MAX_DAG_DEPTH,
    )
    return {r["parent_team_id"] for r in rows}


async def walk_descendants(conn: asyncpg.Connection, team_id: UUID) -> set[UUID]:
    """Return the set of teams that are descendants of ``team_id``."""
    rows = await conn.fetch(
        """
        WITH RECURSIVE descendants AS (
            SELECT member_id AS child_team_id, 1 AS depth
            FROM team_role_assignments
            WHERE parent_team_id = $1 AND member_kind = 'team' AND status = 'active'
            UNION
            SELECT tra.member_id, d.depth + 1
            FROM team_role_assignments tra
            JOIN descendants d ON tra.parent_team_id = d.child_team_id
            WHERE tra.member_kind = 'team' AND tra.status = 'active' AND d.depth < $2
        )
        SELECT DISTINCT child_team_id FROM descendants
        """,
        team_id,
        MAX_DAG_DEPTH,
    )
    return {r["child_team_id"] for r in rows}


async def can_add_team_as_member(
    conn: asyncpg.Connection,
    parent_team_id: UUID,
    candidate_child_id: UUID,
) -> None:
    """Validate that adding ``candidate_child_id`` as a member of ``parent_team_id`` is allowed.

    Raises:
        CycleWouldFormError: if the edge would create a cycle.
        MaxDepthExceededError: if depth would exceed MAX_DAG_DEPTH.

    Caller MUST hold the DAG advisory lock before calling.
    """
    if parent_team_id == candidate_child_id:
        raise CycleWouldFormError("team cannot be a member of itself")

    # Cycle check: candidate must not already be an ancestor of parent.
    parent_ancestors = await walk_ancestors(conn, parent_team_id)
    if candidate_child_id in parent_ancestors:
        raise CycleWouldFormError(
            f"team {candidate_child_id} is already an ancestor of {parent_team_id}"
        )

    # Depth check: existing chains + this edge must stay ≤ MAX_DAG_DEPTH.
    parent_depth = await _max_chain_depth_to_root(conn, parent_team_id)
    child_depth = await _max_chain_depth_from_node(conn, candidate_child_id)
    if parent_depth + 1 + child_depth > MAX_DAG_DEPTH:
        raise MaxDepthExceededError(
            f"adding would create a depth-{parent_depth + 1 + child_depth} chain "
            f"(max {MAX_DAG_DEPTH})"
        )


async def _max_chain_depth_to_root(conn: asyncpg.Connection, team_id: UUID) -> int:
    """Max depth from ``team_id`` upward to a root (team with no parent)."""
    row = await conn.fetchrow(
        """
        WITH RECURSIVE chain AS (
            SELECT $1::uuid AS tid, 0 AS depth
            UNION ALL
            SELECT tra.parent_team_id, c.depth + 1
            FROM team_role_assignments tra
            JOIN chain c
              ON tra.member_kind = 'team' AND tra.member_id = c.tid
            WHERE tra.status = 'active' AND c.depth < $2
        )
        SELECT MAX(depth) AS max_depth FROM chain
        """,
        team_id,
        MAX_DAG_DEPTH,
    )
    return int(row["max_depth"] or 0)


async def _max_chain_depth_from_node(conn: asyncpg.Connection, team_id: UUID) -> int:
    """Max depth from ``team_id`` downward to a leaf."""
    row = await conn.fetchrow(
        """
        WITH RECURSIVE chain AS (
            SELECT $1::uuid AS tid, 0 AS depth
            UNION ALL
            SELECT tra.member_id, c.depth + 1
            FROM team_role_assignments tra
            JOIN chain c ON tra.parent_team_id = c.tid
            WHERE tra.member_kind = 'team' AND tra.status = 'active' AND c.depth < $2
        )
        SELECT MAX(depth) AS max_depth FROM chain
        """,
        team_id,
        MAX_DAG_DEPTH,
    )
    return int(row["max_depth"] or 0)
