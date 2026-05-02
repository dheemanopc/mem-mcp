"""Hybrid retrieval: semantic top-50 ∪ keyword top-50, recency-decayed.

The canonical SQL is in spec §10.3 — replicated here verbatim with positional
parameters. The Python wrapper (hybrid_search) builds the param tuple and
casts result rows to SearchResult.

Scoring formula (spec §10.3):
  score = (w_sem * sem_score + w_kw * (kw_score / max_kw_score))
        * exp(-recency_lambda * age_days)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

# Default semantic / keyword score weights (spec §10.3)
SEARCH_DEFAULT_W_SEM: Final[float] = 0.7
SEARCH_DEFAULT_W_KW: Final[float] = 0.3


@dataclass(frozen=True)
class SearchParams:
    qvec: list[float]
    qtxt: str
    type_: str | None
    tags: list[str] | None
    since: datetime | None
    until: datetime | None
    limit: int
    recency_lambda: float
    w_sem: float = SEARCH_DEFAULT_W_SEM
    w_kw: float = SEARCH_DEFAULT_W_KW


@dataclass(frozen=True)
class SearchResult:
    id: UUID
    content: str
    type: str
    tags: list[str]
    version: int
    created_at: datetime
    updated_at: datetime
    sem_score: float
    kw_score: float
    recency_factor: float
    score: float


_HYBRID_SQL = """
WITH semantic AS (
    SELECT id, 1 - (embedding <=> $1::vector) AS sem_score
    FROM memories
    WHERE tenant_id = $2
      AND deleted_at IS NULL
      AND is_current = true
      AND ($3::text IS NULL OR type = $3)
      AND ($4::text[] IS NULL OR tags && $4)
      AND ($5::timestamptz IS NULL OR created_at >= $5)
      AND ($6::timestamptz IS NULL OR created_at <= $6)
    ORDER BY embedding <=> $1::vector
    LIMIT 50
),
keyword AS (
    SELECT m.id, ts_rank_cd(m.content_tsv, q) AS kw_score
    FROM memories m, plainto_tsquery('english', $7) q
    WHERE m.tenant_id = $2
      AND m.deleted_at IS NULL
      AND m.is_current = true
      AND m.content_tsv @@ q
      AND ($3::text IS NULL OR m.type = $3)
      AND ($4::text[] IS NULL OR m.tags && $4)
      AND ($5::timestamptz IS NULL OR m.created_at >= $5)
      AND ($6::timestamptz IS NULL OR m.created_at <= $6)
    ORDER BY kw_score DESC
    LIMIT 50
),
combined AS (
    SELECT m.id,
           COALESCE(s.sem_score, 0.0) AS sem_score,
           COALESCE(k.kw_score, 0.0)  AS kw_score,
           EXTRACT(EPOCH FROM (now() - m.created_at)) / 86400.0 AS age_days
    FROM memories m
    LEFT JOIN semantic s ON s.id = m.id
    LEFT JOIN keyword  k ON k.id = m.id
    WHERE m.id IN (SELECT id FROM semantic UNION SELECT id FROM keyword)
      AND m.tenant_id = $2
)
SELECT m.id, m.content, m.type, m.tags, m.version,
       m.created_at, m.updated_at,
       c.sem_score, c.kw_score,
       exp(-$8::float * c.age_days) AS recency_factor,
       (
           $9::float * c.sem_score
         + $10::float * (c.kw_score / GREATEST((SELECT MAX(kw_score) FROM keyword), 0.0001))
       ) * exp(-$8::float * c.age_days) AS score
FROM combined c
JOIN memories m ON m.id = c.id
ORDER BY score DESC
LIMIT $11
""".strip()


async def hybrid_search(
    conn: "asyncpg.Connection",
    tenant_id: UUID,
    params: SearchParams,
) -> list[SearchResult]:
    """Run the hybrid query for the tenant. Returns ranked results."""
    rows = await conn.fetch(
        _HYBRID_SQL,
        params.qvec,        # $1
        tenant_id,          # $2
        params.type_,       # $3
        params.tags,        # $4
        params.since,       # $5
        params.until,       # $6
        params.qtxt,        # $7
        params.recency_lambda,  # $8
        params.w_sem,       # $9
        params.w_kw,        # $10
        params.limit,       # $11
    )
    return [
        SearchResult(
            id=r["id"],
            content=r["content"],
            type=r["type"],
            tags=list(r["tags"]),
            version=int(r["version"]),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            sem_score=float(r["sem_score"]),
            kw_score=float(r["kw_score"]),
            recency_factor=float(r["recency_factor"]),
            score=float(r["score"]),
        )
        for r in rows
    ]
