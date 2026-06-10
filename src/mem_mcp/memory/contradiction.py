"""Contradiction candidates on memory_write — deterministic, no LLM (issue #315).

Hard architectural rule: no LLM in the memsys pipeline. The substrate
provides mechanism only; judgment belongs to the actor. So instead of
classifying contradictions server-side, `memory_write` with
`check_contradictions=true` returns the N most recent same-type memories
as *candidates* (plain recency-window SQL — no cosine, no pgvector, no
inference), and the calling model compares them against the content it
just wrote and decides whether anything conflicts.

This keeps the original goal of issue #315 — surface potential
contradictions at write time instead of letting them silently coexist —
while the NLI judgment runs in the client LLM, which is already in the
loop and far better at it than any sidecar model the server could host.

The candidate fetch is advisory and never blocks the write.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

# First 200 chars of candidate content returned to the caller; fetch the
# full body via memory_get when the snippet is not enough to judge.
SNIPPET_MAX_CHARS = 200

_CANDIDATE_SQL = """
SELECT id, content, type, tags, created_at
FROM memories
WHERE tenant_id = $1 AND type = $2
  AND deleted_at IS NULL AND is_current = true
ORDER BY created_at DESC
LIMIT $3
"""


@dataclass(frozen=True)
class ContradictionCandidate:
    memory_id: UUID
    content_snippet: str  # first 200 chars
    type: str
    tags: list[str]
    created_at: datetime


async def find_recent_candidates(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    type_: str,
    *,
    limit: int,
) -> list[ContradictionCandidate]:
    """Return the most recent live same-type memories, newest first.

    Pure recency window — the server makes no claim that any candidate
    contradicts the new content; that judgment is the caller's.
    """
    rows = await conn.fetch(_CANDIDATE_SQL, tenant_id, type_, limit)
    return [
        ContradictionCandidate(
            memory_id=row["id"],
            content_snippet=row["content"][:SNIPPET_MAX_CHARS],
            type=row["type"],
            tags=list(row["tags"] or []),
            created_at=row["created_at"],
        )
        for row in rows
    ]
