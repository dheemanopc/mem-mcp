"""Reference graph helpers — validate-on-write + forward/backward graph + hard-delete protection.

Per memsys foundation 1c47bd99 + test plan e552d271 Spec 4 + amendments:
- #17 (filtered citer list — caller sees UUIDs only for teams they can read;
  inaccessible aggregated as count-only "+N more in inaccessible teams").
- #18 (RLS on memory_references — table-level enforcement; this module's
  helpers operate at the application layer for the opaque-cross-team error
  contract).

Write-time validator: resolves each reference target (UUID or slug-tuple);
on miss OR caller-no-access, rejects with an OPAQUE error that does NOT leak
existence across team boundaries. Both "not found" and "no access" surface
the same error code + same message + (ideally) same DB query count to
prevent timing/probe attacks.

Access predicate (per migration 0035 `can_access_team_resource`): same-tenant
OR UEA-membership. Mirrors the semantics of the read-time memories_select
RLS (migration 0033) so a memory the caller can read is also one they can
reference. Per DA ratification f74663a6 §A(2), the team-membership clause
is UEA-based (DAG-reachable) rather than current_user_active_team_ids()-based
(direct membership) — the writer is strictly more permissive than the reader
on the second clause; eventual convergence is tracked as a follow-up.

Hard-delete protection: BEFORE-DELETE check; if any inbound refs exist,
returns the structured citer list FILTERED to the caller's accessible
teams. Inaccessible citers contribute to an aggregate count only.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]


class ReferenceTargetNotFoundError(Exception):
    """The reference target does not exist OR the caller has no access (opaque)."""


class HardDeleteBlockedError(Exception):
    """Inbound references prevent hard-delete. Carries the filtered citer list."""

    def __init__(
        self,
        message: str,
        *,
        accessible_citers: list[UUID],
        inaccessible_count: int,
    ) -> None:
        super().__init__(message)
        self.accessible_citers = accessible_citers
        self.inaccessible_count = inaccessible_count


ReferenceKind = str  # open-text per amendment; conventional kinds documented elsewhere
RefsVersion = Literal["pinned", "current"]


async def resolve_reference_target(
    conn: asyncpg.Connection,
    *,
    target_uuid: UUID | None = None,
    target_team_id: UUID | None = None,
    target_resource_type: str | None = None,
    target_slug: str | None = None,
    caller_user_id: UUID,
) -> dict[str, Any]:
    """Resolve a reference target to {memory_id, team_id} OR raise opaque error.

    Resolution paths:
      (a) target_uuid given → SELECT FROM memories.
      (b) (target_team_id, target_resource_type, target_slug) given → SELECT FROM slugs.

    Access predicate via `can_access_team_resource(caller, target.tenant_id,
    target.team_id)`: same-tenant OR UEA-membership. Failure (target absent
    OR access denied) raises the SAME ReferenceTargetNotFoundError — caller
    cannot distinguish (IT-08 contract).

    Raises:
        ReferenceTargetNotFoundError: target absent OR caller has no access.
        ValueError: invalid combination of args.
    """
    if target_uuid is not None:
        row = await conn.fetchrow(
            "SELECT id, team_id, tenant_id FROM memories " "WHERE id = $1 AND deleted_at IS NULL",
            target_uuid,
        )
    elif (
        target_team_id is not None and target_resource_type is not None and target_slug is not None
    ):
        row = await conn.fetchrow(
            """
            SELECT m.id, m.team_id, m.tenant_id
            FROM slugs s
            JOIN memories m ON m.id = s.memory_id
            WHERE s.team_id = $1 AND s.resource_type = $2 AND s.slug = $3
              AND m.deleted_at IS NULL
            """,
            target_team_id,
            target_resource_type,
            target_slug,
        )
    else:
        raise ValueError(
            "must provide either target_uuid OR (target_team_id, target_resource_type, target_slug)"
        )

    if row is None:
        raise ReferenceTargetNotFoundError("reference target not found or not accessible")

    allowed = await conn.fetchval(
        "SELECT can_access_team_resource($1, $2, $3)",
        caller_user_id,
        row["tenant_id"],
        row["team_id"],
    )
    if not allowed:
        raise ReferenceTargetNotFoundError("reference target not found or not accessible")

    return {"memory_id": row["id"], "team_id": row["team_id"]}


async def insert_reference(
    conn: asyncpg.Connection,
    *,
    source_memory_id: UUID,
    target_memory_id: UUID,
    target_team_id: UUID,
    reference_kind: ReferenceKind,
    target_fragment: int | None = None,
    refs_version: RefsVersion = "pinned",
) -> None:
    """Insert a memory_references row. Caller is responsible for resolution + access checks."""
    # Coerce None → -1 sentinel (DB column is NOT NULL for PK participation).
    fragment_for_db = -1 if target_fragment is None else target_fragment
    await conn.execute(
        """
        INSERT INTO memory_references
            (source_memory_id, target_memory_id, target_team_id, target_fragment,
             reference_kind, refs_version)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT DO NOTHING
        """,
        source_memory_id,
        target_memory_id,
        target_team_id,
        fragment_for_db,
        reference_kind,
        refs_version,
    )


async def get_inbound_refs(
    conn: asyncpg.Connection,
    *,
    target_memory_id: UUID,
) -> list[dict[str, Any]]:
    """Backward graph: every reference that targets this memory_id.

    Joins source memory to surface source_team_id + source_tenant_id so
    callers can run access predicates without per-row lookups. No access
    filtering here — caller layers it on top.
    """
    rows = await conn.fetch(
        """
        SELECT mr.source_memory_id, mr.target_memory_id, mr.target_team_id,
               mr.target_fragment, mr.reference_kind, mr.refs_version, mr.created_at,
               sm.team_id AS source_team_id, sm.tenant_id AS source_tenant_id
        FROM memory_references mr
        LEFT JOIN memories sm ON sm.id = mr.source_memory_id
        WHERE mr.target_memory_id = $1
        """,
        target_memory_id,
    )
    return [dict(r) for r in rows]


async def get_outbound_refs(
    conn: asyncpg.Connection,
    *,
    source_memory_id: UUID,
) -> list[dict[str, Any]]:
    """Forward graph: every reference this memory makes.

    Joins target memory to surface target_tenant_id so callers can run
    access predicates without per-row lookups. (target_team_id is already
    on memory_references.) LEFT JOIN handles the rare hard-deleted-target
    case — target_tenant_id IS NULL falls through to UEA check.
    """
    rows = await conn.fetch(
        """
        SELECT mr.source_memory_id, mr.target_memory_id, mr.target_team_id,
               mr.target_fragment, mr.reference_kind, mr.refs_version, mr.created_at,
               tm.tenant_id AS target_tenant_id
        FROM memory_references mr
        LEFT JOIN memories tm ON tm.id = mr.target_memory_id
        WHERE mr.source_memory_id = $1
        """,
        source_memory_id,
    )
    return [dict(r) for r in rows]


async def check_hard_delete_with_filtered_citers(
    conn: asyncpg.Connection,
    *,
    target_memory_id: UUID,
    caller_user_id: UUID,
) -> None:
    """Raise HardDeleteBlockedError if inbound refs exist.

    Per amendment #17: the error's citer list is FILTERED to only memory UUIDs
    the caller can access via can_access_team_resource (same-tenant OR
    UEA-membership). Inaccessible citers contribute to an aggregate count
    only — caller never learns their UUIDs OR which teams they're in.
    Preserves the cross-team opaque-existence model.

    Returns silently when zero inbound refs (caller proceeds with delete).
    """
    rows = await conn.fetch(
        """
        SELECT mr.source_memory_id,
               m.team_id AS source_team_id,
               m.tenant_id AS source_tenant_id
        FROM memory_references mr
        JOIN memories m ON m.id = mr.source_memory_id
        WHERE mr.target_memory_id = $1
        """,
        target_memory_id,
    )
    if not rows:
        return

    accessible: list[UUID] = []
    inaccessible_count = 0
    for r in rows:
        allowed = await conn.fetchval(
            "SELECT can_access_team_resource($1, $2, $3)",
            caller_user_id,
            r["source_tenant_id"],
            r["source_team_id"],
        )
        if allowed:
            accessible.append(r["source_memory_id"])
        else:
            inaccessible_count += 1

    summary_parts = []
    if accessible:
        summary_parts.append(f"{len(accessible)} accessible: {accessible}")
    if inaccessible_count:
        summary_parts.append(f"+{inaccessible_count} more in inaccessible teams")
    summary = "; ".join(summary_parts) if summary_parts else "no citers"
    raise HardDeleteBlockedError(
        f"cannot hard-delete: {summary}",
        accessible_citers=accessible,
        inaccessible_count=inaccessible_count,
    )
