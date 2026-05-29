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
    tenant_id: UUID,
) -> dict[str, Any]:
    """Resolve a reference target to {memory_id, team_id} OR raise opaque error.

    Resolution paths:
      (a) target_uuid given → SELECT FROM memories.
      (b) (target_team_id, target_resource_type, target_slug) given → SELECT FROM slugs.

    Access check: caller MUST have an active user_effective_team_access row
    for the target's team. Failure (target absent OR access denied) raises
    the SAME ReferenceTargetNotFoundError — caller cannot distinguish.

    Raises:
        ReferenceTargetNotFoundError: target absent OR caller has no access.
        ValueError: invalid combination of args.
    """
    if target_uuid is not None:
        row = await conn.fetchrow(
            "SELECT id, team_id FROM memories WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL",
            target_uuid,
            tenant_id,
        )
    elif (
        target_team_id is not None and target_resource_type is not None and target_slug is not None
    ):
        row = await conn.fetchrow(
            """
            SELECT m.id, m.team_id
            FROM slugs s
            JOIN memories m ON m.id = s.memory_id
            WHERE s.team_id = $1 AND s.resource_type = $2 AND s.slug = $3
              AND m.tenant_id = $4 AND m.deleted_at IS NULL
            """,
            target_team_id,
            target_resource_type,
            target_slug,
            tenant_id,
        )
    else:
        raise ValueError(
            "must provide either target_uuid OR (target_team_id, target_resource_type, target_slug)"
        )

    if row is None:
        raise ReferenceTargetNotFoundError("reference target not found or not accessible")

    # Access check: opaque-fail for cross-team unreadable.
    if row["team_id"] is not None:
        access = await conn.fetchval(
            """
            SELECT 1 FROM user_effective_team_access
             WHERE user_id = $1 AND resource_team_id = $2
            """,
            caller_user_id,
            row["team_id"],
        )
        if access is None:
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
        target_fragment,
        reference_kind,
        refs_version,
    )


async def get_inbound_refs(
    conn: asyncpg.Connection,
    *,
    target_memory_id: UUID,
) -> list[dict[str, Any]]:
    """Backward graph: every reference that targets this memory_id.

    No access filtering here — caller layers it on top via filter_citers_by_access.
    """
    rows = await conn.fetch(
        """
        SELECT source_memory_id, target_memory_id, target_team_id,
               target_fragment, reference_kind, refs_version, created_at
        FROM memory_references
        WHERE target_memory_id = $1
        """,
        target_memory_id,
    )
    return [dict(r) for r in rows]


async def get_outbound_refs(
    conn: asyncpg.Connection,
    *,
    source_memory_id: UUID,
) -> list[dict[str, Any]]:
    """Forward graph: every reference this memory makes."""
    rows = await conn.fetch(
        """
        SELECT source_memory_id, target_memory_id, target_team_id,
               target_fragment, reference_kind, refs_version, created_at
        FROM memory_references
        WHERE source_memory_id = $1
        """,
        source_memory_id,
    )
    return [dict(r) for r in rows]


async def check_hard_delete_with_filtered_citers(
    conn: asyncpg.Connection,
    *,
    target_memory_id: UUID,
    caller_user_id: UUID,
    tenant_id: UUID,
) -> None:
    """Raise HardDeleteBlockedError if inbound refs exist.

    Per amendment #17: the error's citer list is FILTERED to only memory UUIDs
    in teams the caller can read. Inaccessible citers contribute to an aggregate
    count only — caller never learns their UUIDs OR which teams they're in.
    Preserves the cross-team opaque-existence model.

    Returns silently when zero inbound refs (caller proceeds with delete).
    """
    rows = await conn.fetch(
        """
        SELECT mr.source_memory_id, m.team_id AS source_team_id
        FROM memory_references mr
        JOIN memories m ON m.id = mr.source_memory_id
        WHERE mr.target_memory_id = $1 AND m.tenant_id = $2
        """,
        target_memory_id,
        tenant_id,
    )
    if not rows:
        return

    # Per-citer access check via user_effective_team_access lookup.
    accessible: list[UUID] = []
    inaccessible_count = 0
    for r in rows:
        access = await conn.fetchval(
            "SELECT 1 FROM user_effective_team_access "
            "WHERE user_id = $1 AND resource_team_id = $2",
            caller_user_id,
            r["source_team_id"],
        )
        if access is not None:
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
