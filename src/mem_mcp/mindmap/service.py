"""Mind-map DB helpers — operate on a tenant-scoped asyncpg connection.

These are thin, deterministic store operations (no judgment — the
no-intelligence principle). The MCP tools orchestrate them around the core
memory-write path. All functions assume the caller has already opened a
``tenant_tx`` (so ``app.current_tenant_id`` is set and RLS is satisfied).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
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
    return await conn.fetchval(
        "SELECT memory_id FROM slugs WHERE team_id = $1 AND resource_type = 'map' AND slug = $2",
        team_id,
        map_key,
    )


async def find_live_map_seeded_from(
    conn: asyncpg.Connection, *, seed_spec_memory_id: UUID
) -> UUID | None:
    """from_spec dedup guard: an existing LIVE map seeded from this spec node."""
    return await conn.fetchval(
        """
        SELECT root_memory_id FROM memory_maps
        WHERE seed_spec_memory_id = $1 AND state = 'live'
        LIMIT 1
        """,
        seed_spec_memory_id,
    )


async def insert_membership(
    conn: asyncpg.Connection,
    *,
    memory_id: UUID,
    root_memory_id: UUID,
    tenant_id: UUID,
    node_role: str,
) -> None:
    """Record exclusive ownership of a node by a map.

    The PRIMARY KEY on memory_id raises UniqueViolationError if the node is
    already owned by any map — exclusive ownership is enforced by the store,
    not the caller.
    """
    await conn.execute(
        """
        INSERT INTO memory_map_membership (memory_id, root_memory_id, tenant_id, node_role)
        VALUES ($1, $2, $3, $4)
        """,
        memory_id,
        root_memory_id,
        tenant_id,
        node_role,
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
    conn: asyncpg.Connection, *, root_memory_id: UUID, tenant_id: UUID
) -> list[dict[str, Any]]:
    """All nodes owned by the map, joined to their memory content.

    Explicitly tenant-scoped (defense-in-depth on top of RLS).
    """
    rows = await conn.fetch(
        """
        SELECT mm.memory_id, mm.node_role, mm.added_at,
               m.content, m.type, m.metadata, m.created_at
        FROM memory_map_membership mm
        JOIN memories m ON m.id = mm.memory_id
        WHERE mm.root_memory_id = $1 AND m.tenant_id = $2 AND m.deleted_at IS NULL
        ORDER BY mm.added_at ASC
        """,
        root_memory_id,
        tenant_id,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["metadata"] = _as_dict(d.get("metadata"))
        out.append(d)
    return out


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
