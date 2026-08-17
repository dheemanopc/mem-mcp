"""Mind-map DB helpers — operate on a tenant-scoped asyncpg connection.

These are thin, deterministic store operations (no judgment — the
no-intelligence principle). The MCP tools orchestrate them around the core
memory-write path. All functions assume the caller has already opened a
``tenant_tx`` (so ``app.current_tenant_id`` is set and RLS is satisfied).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


def _as_dict(value: Any) -> dict[str, Any]:
    """asyncpg returns JSONB as a str when no codec is registered; accept both."""
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value)


async def insert_map(
    conn: asyncpg.Connection,
    *,
    root_memory_id: UUID,
    tenant_id: UUID,
    team_id: UUID,
    title: str,
    review_threshold: int,
    seed_spec_memory_id: UUID | None = None,
) -> None:
    """Create the memory_maps row for a freshly-rooted map."""
    await conn.execute(
        """
        INSERT INTO memory_maps
            (root_memory_id, tenant_id, team_id, title, review_threshold, seed_spec_memory_id)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        root_memory_id,
        tenant_id,
        team_id,
        title,
        review_threshold,
        seed_spec_memory_id,
    )


async def get_map_row(conn: asyncpg.Connection, *, root_memory_id: UUID) -> dict[str, Any] | None:
    """Fetch a single map row (or None)."""
    row = await conn.fetchrow(
        "SELECT * FROM memory_maps WHERE root_memory_id = $1",
        root_memory_id,
    )
    return dict(row) if row else None


async def resolve_map_root(conn: asyncpg.Connection, *, team_id: UUID, map_key: str) -> UUID | None:
    """Resolve a map key (the root memory's 'map' slug) to its root_memory_id."""
    root_id = await conn.fetchval(
        "SELECT memory_id FROM slugs WHERE team_id = $1 AND resource_type = 'map' AND slug = $2",
        team_id,
        map_key,
    )
    return cast("UUID | None", root_id)


async def find_live_map_seeded_from(
    conn: asyncpg.Connection, *, seed_spec_memory_id: UUID
) -> UUID | None:
    """from_spec dedup guard: an existing LIVE map seeded from this spec node."""
    root_id = await conn.fetchval(
        """
        SELECT root_memory_id FROM memory_maps
        WHERE seed_spec_memory_id = $1 AND state = 'live'
        LIMIT 1
        """,
        seed_spec_memory_id,
    )
    return cast("UUID | None", root_id)


async def insert_membership(
    conn: asyncpg.Connection,
    *,
    memory_id: UUID,
    root_memory_id: UUID,
    tenant_id: UUID,
    node_role: str,
    turn: str | None = None,
    authored_by: str | None = None,
    decision_criteria: str | None = None,
) -> None:
    """Record exclusive ownership of a node by a map.

    The PRIMARY KEY on memory_id raises UniqueViolationError if the node is
    already owned by any map — exclusive ownership is enforced by the store,
    not the caller.

    ``turn`` mirrors metadata.responsible_party into a real column so the
    open-loop query is indexed. ``authored_by`` is deliberately separate: turn is
    whose MOVE it is, authored_by is who WROTE the node, and the graduation guard
    needs the latter. ``decision_criteria`` is what makes a question answerable —
    and actionable on cold reload — without its branch in context.
    """
    await conn.execute(
        """
        INSERT INTO memory_map_membership
            (memory_id, root_memory_id, tenant_id, node_role, turn, authored_by,
             decision_criteria)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        memory_id,
        root_memory_id,
        tenant_id,
        node_role,
        turn,
        authored_by,
        decision_criteria,
    )


async def bump_review_counter(conn: asyncpg.Connection, *, root_memory_id: UUID) -> tuple[int, int]:
    """Increment writes_since_review; return (new_count, review_threshold)."""
    row = await conn.fetchrow(
        """
        UPDATE memory_maps
           SET writes_since_review = writes_since_review + 1
         WHERE root_memory_id = $1
        RETURNING writes_since_review, review_threshold
        """,
        root_memory_id,
    )
    return int(row["writes_since_review"]), int(row["review_threshold"])


async def reset_review_counter(conn: asyncpg.Connection, *, root_memory_id: UUID) -> None:
    await conn.execute(
        "UPDATE memory_maps SET writes_since_review = 0 WHERE root_memory_id = $1",
        root_memory_id,
    )


async def archive_map(
    conn: asyncpg.Connection,
    *,
    root_memory_id: UUID,
    graduated_index_id: UUID | None,
) -> None:
    await conn.execute(
        """
        UPDATE memory_maps
           SET state = 'archived', closed_at = now(), graduated_index_id = $2
         WHERE root_memory_id = $1
        """,
        root_memory_id,
        graduated_index_id,
    )


async def log_event(
    conn: asyncpg.Connection,
    *,
    root_memory_id: UUID,
    tenant_id: UUID,
    event: str,
    actor: str,
    memory_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append an observability event (day one — spec 0aa54585)."""
    await conn.execute(
        """
        INSERT INTO memory_map_events
            (root_memory_id, tenant_id, memory_id, event, actor, payload)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        """,
        root_memory_id,
        tenant_id,
        memory_id,
        event,
        actor,
        json.dumps(payload or {}),
    )


async def fetch_members(
    conn: asyncpg.Connection,
    *,
    root_memory_id: UUID,
    tenant_id: UUID,
    statuses: list[str] | None = None,
    since_memory_ids: list[UUID] | None = None,
) -> list[dict[str, Any]]:
    """All nodes owned by the map, joined to their memory content.

    Explicitly tenant-scoped (defense-in-depth on top of RLS).

    ``statuses`` filters by branch status ('open' / 'resolved' / 'dropped');
    None means no filter. ``since_memory_ids`` restricts to a specific set —
    used by the delta read to return only nodes touched since a cursor.
    Passing an EMPTY list means "no nodes matched", not "no filter": the caller
    already knows nothing changed, so return nothing rather than everything.
    """
    if since_memory_ids is not None and not since_memory_ids:
        return []
    clauses = ["mm.root_memory_id = $1", "m.tenant_id = $2", "m.deleted_at IS NULL"]
    args: list[Any] = [root_memory_id, tenant_id]
    if statuses is not None:
        args.append(statuses)
        clauses.append(f"mm.status = ANY(${len(args)}::text[])")
    if since_memory_ids is not None:
        args.append(since_memory_ids)
        clauses.append(f"mm.memory_id = ANY(${len(args)}::uuid[])")
    rows = await conn.fetch(
        f"""
        SELECT mm.memory_id, mm.node_role, mm.added_at, mm.status, mm.turn,
               mm.authored_by,
               mm.resolution_summary, mm.resolved_by_memory_id, mm.resolved_by_party,
               mm.resolved_at, mm.decision_criteria,
               m.content, m.type, m.metadata, m.created_at
        FROM memory_map_membership mm
        JOIN memories m ON m.id = mm.memory_id
        WHERE {" AND ".join(clauses)}
        ORDER BY mm.added_at ASC
        """,
        *args,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["metadata"] = _as_dict(d.get("metadata"))
        out.append(d)
    return out


async def fetch_memory_ids_since(
    conn: asyncpg.Connection, *, root_memory_id: UUID, since: int
) -> tuple[list[UUID], int]:
    """Memory ids touched by events after ``since``, plus the new cursor.

    ``memory_map_events.id`` is a BIGSERIAL on an append-only log, so it is
    already a monotonic cursor — no extra sequence or timestamp comparison.
    Returns ([], since) when nothing changed, so a no-op resume costs one
    indexed query and sends no nodes at all.
    """
    rows = await conn.fetch(
        """
        SELECT DISTINCT memory_id
        FROM memory_map_events
        WHERE root_memory_id = $1 AND id > $2 AND memory_id IS NOT NULL
        """,
        root_memory_id,
        since,
    )
    head = await current_cursor(conn, root_memory_id=root_memory_id)
    return [r["memory_id"] for r in rows], head


async def current_cursor(conn: asyncpg.Connection, *, root_memory_id: UUID) -> int:
    """Highest event id for the map; 0 when it has no events yet."""
    val = await conn.fetchval(
        "SELECT COALESCE(MAX(id), 0) FROM memory_map_events WHERE root_memory_id = $1",
        root_memory_id,
    )
    return int(val or 0)


async def set_node_status(
    conn: asyncpg.Connection,
    *,
    memory_id: UUID,
    root_memory_id: UUID,
    status: str,
    resolution_summary: str,
    resolved_by_memory_id: UUID | None,
    resolved_by_party: str,
) -> bool:
    """Mark a branch settled. Returns False when the node isn't in this map.

    Scoped by root_memory_id as well as memory_id so a caller cannot resolve a
    node belonging to a different map by guessing its id.
    """
    result = await conn.execute(
        """
        UPDATE memory_map_membership
           SET status = $3,
               resolution_summary = $4,
               resolved_by_memory_id = $5,
               resolved_by_party = $6,
               resolved_at = now()
         WHERE memory_id = $1 AND root_memory_id = $2
        """,
        memory_id,
        root_memory_id,
        status,
        resolution_summary,
        resolved_by_memory_id,
        resolved_by_party,
    )
    return str(result).split()[-1] != "0"


async def reopen_node(
    conn: asyncpg.Connection,
    *,
    memory_id: UUID,
    root_memory_id: UUID,
    turn: str,
) -> bool:
    """Reopen a settled branch: status back to 'open', turn handed on, all
    resolution fields cleared. Returns False when the node isn't in this map.

    Scoped by root_memory_id as well as memory_id so a caller cannot reopen a
    node belonging to a different map by guessing its id (same guard as
    set_node_status).
    """
    result = await conn.execute(
        """
        UPDATE memory_map_membership
           SET status = 'open',
               turn = $3,
               resolution_summary = NULL,
               resolved_by_memory_id = NULL,
               resolved_by_party = NULL,
               resolved_at = NULL
         WHERE memory_id = $1 AND root_memory_id = $2
        """,
        memory_id,
        root_memory_id,
        turn,
    )
    return str(result).split()[-1] != "0"


async def touch_owner_write(conn: asyncpg.Connection, *, root_memory_id: UUID) -> None:
    """Record that the owner wrote, for the graduation guard."""
    await conn.execute(
        "UPDATE memory_maps SET last_owner_write_at = now() WHERE root_memory_id = $1",
        root_memory_id,
    )


async def touch_agent_read(conn: asyncpg.Connection, *, root_memory_id: UUID) -> None:
    """Record that the agent read the map, for the graduation guard."""
    await conn.execute(
        "UPDATE memory_maps SET last_agent_read_at = now() WHERE root_memory_id = $1",
        root_memory_id,
    )


async def unacknowledged_owner_input(
    conn: asyncpg.Connection, *, root_memory_id: UUID, tenant_id: UUID
) -> list[dict[str, Any]]:
    """Owner-AUTHORED nodes written after the agent's last read.

    Empty list means the agent has seen everything the owner said. Non-empty
    means graduating now would bury human input the model never read.

    Keys on ``authored_by``, never ``turn``. Using turn would break this in both
    directions: an agent asking a question sets turn='owner' and would trip the
    guard on its own question, while an owner answering hands the turn back to
    'model' and their input would stop counting — the exact case the guard is
    for.
    """
    row = await conn.fetchrow(
        "SELECT last_owner_write_at, last_agent_read_at FROM memory_maps WHERE root_memory_id = $1",
        root_memory_id,
    )
    if row is None or row["last_owner_write_at"] is None:
        return []
    last_read = row["last_agent_read_at"]
    if last_read is not None and row["last_owner_write_at"] <= last_read:
        return []
    rows = await conn.fetch(
        """
        SELECT mm.memory_id, m.content
        FROM memory_map_membership mm
        JOIN memories m ON m.id = mm.memory_id
        WHERE mm.root_memory_id = $1 AND m.tenant_id = $2
          AND mm.authored_by = 'owner' AND m.deleted_at IS NULL
          AND ($3::timestamptz IS NULL OR mm.added_at > $3)
        ORDER BY mm.added_at ASC
        """,
        root_memory_id,
        tenant_id,
        last_read,
    )
    return [dict(r) for r in rows]


async def list_maps(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    team_id: UUID | None = None,
    state: str | None = "live",
    has_open_loops: bool | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Maps visible to the caller, with their open-loop counts.

    There is no enumeration verb today, so neither an explorer nor a
    cross-map answer queue is buildable without this.
    """
    clauses = ["mp.tenant_id = $1"]
    args: list[Any] = [tenant_id]
    if team_id is not None:
        args.append(team_id)
        clauses.append(f"mp.team_id = ${len(args)}")
    if state is not None and state != "all":
        args.append(state)
        clauses.append(f"mp.state = ${len(args)}")
    args.append(limit)
    having = ""
    if has_open_loops is True:
        having = "HAVING COUNT(mm.memory_id) > 0"
    elif has_open_loops is False:
        having = "HAVING COUNT(mm.memory_id) = 0"
    # map_key is the root memory's 'map' slug — it lives in `slugs`, not on
    # memory_maps (see resolve_map_root).
    rows = await conn.fetch(
        f"""
        SELECT mp.root_memory_id, s.slug AS map_key, mp.title, mp.state,
               mp.last_owner_write_at, mp.created_at,
               COUNT(mm.memory_id) AS open_loop_count
        FROM memory_maps mp
        JOIN slugs s
          ON s.memory_id = mp.root_memory_id
         AND s.team_id = mp.team_id
         AND s.resource_type = 'map'
        LEFT JOIN memory_map_membership mm
               ON mm.root_memory_id = mp.root_memory_id
              AND mm.status = 'open' AND mm.turn = 'owner'
        WHERE {" AND ".join(clauses)}
        GROUP BY mp.root_memory_id, s.slug, mp.title, mp.state,
                 mp.last_owner_write_at, mp.created_at
        {having}
        ORDER BY mp.last_owner_write_at DESC NULLS LAST, mp.created_at DESC
        LIMIT ${len(args)}
        """,
        *args,
    )
    return [dict(r) for r in rows]


async def fetch_open_questions(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    team_id: UUID | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Every unanswered owner-turn question across all live maps.

    This is the answer queue: it is what lets several threads be answered in
    parallel, out of band, instead of one per conversational turn.
    """
    # turn='owner' means it is the owner's MOVE — a reply the owner writes
    # hands the turn back ('model'), so their own contributions do not pile up
    # in their own queue. status='open' is what retires an answered ask.
    clauses = ["mp.tenant_id = $1", "mp.state = 'live'", "mm.status = 'open'", "mm.turn = 'owner'"]
    args: list[Any] = [tenant_id]
    if team_id is not None:
        args.append(team_id)
        clauses.append(f"mp.team_id = ${len(args)}")
    args.append(limit)
    rows = await conn.fetch(
        f"""
        SELECT s.slug AS map_key, mp.title AS map_title, mm.memory_id, mm.node_role,
               mm.decision_criteria, mm.added_at, m.content
        FROM memory_map_membership mm
        JOIN memory_maps mp ON mp.root_memory_id = mm.root_memory_id
        JOIN slugs s
          ON s.memory_id = mp.root_memory_id
         AND s.team_id = mp.team_id
         AND s.resource_type = 'map'
        JOIN memories m ON m.id = mm.memory_id
        WHERE {" AND ".join(clauses)} AND m.deleted_at IS NULL
        ORDER BY mm.added_at ASC
        LIMIT ${len(args)}
        """,
        *args,
    )
    return [dict(r) for r in rows]


async def fetch_internal_edges(
    conn: asyncpg.Connection, *, member_ids: list[UUID]
) -> list[dict[str, Any]]:
    """Edges whose source AND target are both members of the map."""
    if not member_ids:
        return []
    rows = await conn.fetch(
        """
        SELECT source_memory_id, target_memory_id, reference_kind, created_at
        FROM memory_references
        WHERE source_memory_id = ANY($1::uuid[])
          AND target_memory_id = ANY($1::uuid[])
        ORDER BY created_at ASC
        """,
        member_ids,
    )
    return [dict(r) for r in rows]


async def fetch_recent_events(
    conn: asyncpg.Connection, *, root_memory_id: UUID, limit: int = 50
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT event, actor, memory_id, payload, created_at
        FROM memory_map_events
        WHERE root_memory_id = $1
        ORDER BY created_at DESC, id DESC
        LIMIT $2
        """,
        root_memory_id,
        limit,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["payload"] = _as_dict(d.get("payload"))
        out.append(d)
    return out
