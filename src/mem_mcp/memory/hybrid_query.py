"""Hybrid retrieval: semantic top-50 U keyword top-50, recency-decayed.

Based on the canonical SQL in spec §10.3, with two deviations driven by
user feedback (recall complaints + context-heavy responses):
- the keyword leg uses a lenient OR-of-lexemes tsquery instead of
  plainto_tsquery's all-words AND (any matching word qualifies; ts_rank_cd
  still ranks fuller matches higher);
- each returned row carries a `preview` ts_headline snippet for relevance
  triage, computed only on the post-LIMIT rows.

Scoring formula (spec §10.3, unchanged):
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

# Lenient OR tsquery built from the caller's plain-text query: normalize the
# text into lexemes via to_tsvector, then OR them together. Any single
# matching word qualifies a row for the keyword leg (ts_rank_cd still ranks
# multi-word matches higher), unlike plainto_tsquery which ANDs every word
# and zeroes the keyword score for any memory missing one term. Lexemes are
# quote_literal-quoted so punctuation inside a lexeme can't break tsquery
# syntax. All-stopword input aggregates to NULL → to_tsquery(NULL) → NULL,
# which every consumer must treat as "no keyword leg".
# {param} is the positional parameter carrying the raw query text.
LENIENT_TSQUERY_SQL: Final[str] = """to_tsquery('english',
    (SELECT string_agg(quote_literal(lexeme), ' | ')
     FROM unnest(tsvector_to_array(to_tsvector('english', {param}))) AS lexeme)
)""".strip()

# ts_headline options for result previews: short keyword-windowed fragments,
# **bold** match markers (markdown, readable by both humans and LLM clients).
HEADLINE_OPTIONS: Final[str] = (
    "StartSel=**, StopSel=**, MaxFragments=2, MaxWords=30, MinWords=8, " 'FragmentDelimiter=" … "'
)

# ts_headline cost scales with document length; cap its input. Matches past
# this window fall back to the head-of-content preview.
HEADLINE_SOURCE_MAX_CHARS: Final[int] = 8_000

# Fallback preview (no keyword match / all-stopword query): head of content.
PREVIEW_FALLBACK_CHARS: Final[int] = 280

# Hard cap on the preview field itself.
PREVIEW_MAX_CHARS: Final[int] = 600


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
    parent_id: UUID | None = None
    include_expired: bool = False


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
    indexable: bool
    embedding_status: str
    # Keyword-windowed ts_headline snippet (**match** markers) when the query
    # keyword-matched the row; head-of-content otherwise. For relevance triage
    # — full body via memory_get.
    preview: str


_HYBRID_SQL = f"""
WITH q AS (
    SELECT {LENIENT_TSQUERY_SQL.format(param="$7")} AS tsq
),
semantic AS (
    SELECT id, 1 - (embedding <=> $1::vector) AS sem_score
    FROM memories
    WHERE tenant_id = $2
      AND deleted_at IS NULL
      AND is_current = true
      AND indexable = true  -- opt-out rows are not searchable (any leg)
      AND embedding IS NOT NULL  -- oversize content stored without embedding
      AND ($3::text IS NULL OR type = $3)
      AND ($4::text[] IS NULL OR tags @> $4)
      AND ($5::timestamptz IS NULL OR created_at >= $5)
      AND ($6::timestamptz IS NULL OR created_at <= $6)
      AND ($12::uuid IS NULL OR parent_id = $12::uuid)
      AND ($13::boolean OR expires_at IS NULL OR expires_at > NOW())
    ORDER BY embedding <=> $1::vector
    LIMIT 50
),
keyword AS (
    SELECT m.id, ts_rank_cd(m.content_tsv, q.tsq) AS kw_score
    FROM memories m, q
    WHERE m.tenant_id = $2
      AND m.deleted_at IS NULL
      AND m.is_current = true
      AND m.indexable = true  -- indexable=false skips embeddings only, so
                              -- without this guard opt-out rows leak back
                              -- in through lexical matching
      AND q.tsq IS NOT NULL
      AND m.content_tsv @@ q.tsq
      AND ($3::text IS NULL OR m.type = $3)
      AND ($4::text[] IS NULL OR m.tags @> $4)
      AND ($5::timestamptz IS NULL OR m.created_at >= $5)
      AND ($6::timestamptz IS NULL OR m.created_at <= $6)
      AND ($12::uuid IS NULL OR m.parent_id = $12::uuid)
      AND ($13::boolean OR m.expires_at IS NULL OR m.expires_at > NOW())
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
),
ranked AS (
    SELECT m.id, m.content, m.type, m.tags, m.version,
           m.created_at, m.updated_at,
           c.sem_score, c.kw_score,
           exp(-$8::float * c.age_days) AS recency_factor,
           (
               $9::float * c.sem_score
             + $10::float * (c.kw_score / GREATEST((SELECT MAX(kw_score) FROM keyword), 0.0001))
           ) * exp(-$8::float * c.age_days) AS score,
           m.indexable, m.embedding_status
    FROM combined c
    JOIN memories m ON m.id = c.id
    ORDER BY score DESC
    LIMIT $11
)
-- preview computed only on the LIMITed rows (ts_headline is per-row costly)
SELECT r.*,
       LEFT(
           COALESCE(
               ts_headline('english', LEFT(r.content, {HEADLINE_SOURCE_MAX_CHARS}),
                           (SELECT tsq FROM q), '{HEADLINE_OPTIONS}'),
               LEFT(r.content, {PREVIEW_FALLBACK_CHARS})
           ),
           {PREVIEW_MAX_CHARS}
       ) AS preview
FROM ranked r
ORDER BY r.score DESC
""".strip()


async def hybrid_search(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    params: SearchParams,
) -> list[SearchResult]:
    """Run the hybrid query for the tenant. Returns ranked results."""
    rows = await conn.fetch(
        _HYBRID_SQL,
        params.qvec,  # $1
        tenant_id,  # $2
        params.type_,  # $3
        params.tags,  # $4
        params.since,  # $5
        params.until,  # $6
        params.qtxt,  # $7
        params.recency_lambda,  # $8
        params.w_sem,  # $9
        params.w_kw,  # $10
        params.limit,  # $11
        params.parent_id,  # $12
        params.include_expired,  # $13
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
            indexable=bool(r["indexable"]),
            embedding_status=r["embedding_status"],
            preview=r["preview"],
        )
        for r in rows
    ]
