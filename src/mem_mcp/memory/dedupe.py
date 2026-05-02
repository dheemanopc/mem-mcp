"""Dedupe primitives: hash-match THEN cosine-similarity match.

Per spec §10.5 + LLD §4.5.2:
  1. Hash check (exact, indexed): SELECT WHERE tenant_id AND content_hash AND
     deleted_at IS NULL AND is_current=true LIMIT 1.
  2. Embedding check (only if no hash match AND embedding provided): SELECT
     ... type=? ORDER BY embedding<=>? LIMIT 1; return if sim > 0.95.

Returns the existing memory id + match-kind ('hash' or 'embedding'), or None.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


_DEDUPE_COSINE_THRESHOLD = 0.95

DedupeKind = Literal["hash", "embedding"]


@dataclass(frozen=True)
class DedupeMatch:
    existing_id: UUID
    kind: DedupeKind


async def check_dup(
    conn: "asyncpg.Connection",
    tenant_id: UUID,
    content_hash: str,
    embedding: list[float] | None,
    type_: str,
) -> DedupeMatch | None:
    """Check for hash- or embedding-level duplicates within the tenant.

    Returns DedupeMatch on hit, None otherwise. The caller decides what to
    do (typically: union the new tags into the existing row + bump
    updated_at; per spec §9.3.1.8).
    """
    # 1. Exact hash match (indexed; cheap)
    row = await conn.fetchrow(
        """
        SELECT id FROM memories
        WHERE tenant_id = $1 AND content_hash = $2
          AND deleted_at IS NULL AND is_current = true
        LIMIT 1
        """,
        tenant_id,
        content_hash,
    )
    if row is not None:
        return DedupeMatch(existing_id=row["id"], kind="hash")

    if embedding is None:
        return None

    # 2. Cosine similarity (HNSW index; type-scoped to keep search small)
    row = await conn.fetchrow(
        """
        SELECT id, 1 - (embedding <=> $3::vector) AS sim
        FROM memories
        WHERE tenant_id = $1 AND type = $2
          AND deleted_at IS NULL AND is_current = true
        ORDER BY embedding <=> $3::vector
        LIMIT 1
        """,
        tenant_id,
        type_,
        embedding,
    )
    if row is None:
        return None

    sim = float(row["sim"])
    if sim > _DEDUPE_COSINE_THRESHOLD:
        return DedupeMatch(existing_id=row["id"], kind="embedding")
    return None
