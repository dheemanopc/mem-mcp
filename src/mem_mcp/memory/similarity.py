"""Pgvector-backed similarity primitives + cluster-build BFS.

Two callers:

1. Web endpoint POST /api/web/memories/management/similar — calls
   ``find_similar`` to surface top-K memories cosine-similar to a seed
   (either a memory_id or free-text whose embedding the caller resolved).

2. Weekly job mem_mcp.jobs.cluster_build — calls ``build_clusters`` to
   walk the corpus, BFS the similarity edge-graph, and write each
   connected component (size >= 2) as a row in memory_clusters.

Both rely on the existing HNSW(vector_cosine_ops) index on
memories.embedding shipped in migration 0001.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


# Default cosine-similarity threshold for "near-duplicate".
# 0.85 catches close rephrasings without sweeping in weakly-related topics.
DEFAULT_SIMILARITY_THRESHOLD = 0.85

# Cap pgvector neighbor queries to avoid pulling unbounded result sets when
# someone has 50 near-clones of one memory (rare but possible).
_MAX_NEIGHBORS_PER_QUERY = 100


@dataclass
class SimilarMemory:
    id: UUID
    content: str
    type: str
    tags: list[str]
    similarity: float  # cosine similarity, in [-1, 1] but in practice ~[0, 1] for English text


async def find_similar(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    seed_embedding: list[float],
    *,
    exclude_id: UUID | None = None,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    limit: int = 20,
) -> list[SimilarMemory]:
    """Return memories whose embedding is cosine-similar to seed_embedding,
    above threshold, ordered by similarity descending.

    pgvector's ``<=>`` is cosine *distance* (0..2 for unit-normalized
    vectors); cosine *similarity* = 1 - distance.
    """
    rows = await conn.fetch(
        """
        SELECT id, content, type, tags, 1 - (embedding <=> $1::vector) AS similarity
        FROM memories
        WHERE tenant_id = $2
          AND deleted_at IS NULL
          AND is_current = true
          AND embedding IS NOT NULL
          AND ($3::uuid IS NULL OR id <> $3)
          AND 1 - (embedding <=> $1::vector) >= $4
        ORDER BY embedding <=> $1::vector
        LIMIT $5
        """,
        seed_embedding,
        tenant_id,
        exclude_id,
        threshold,
        limit,
    )
    return [
        SimilarMemory(
            id=r["id"],
            content=r["content"],
            type=r["type"],
            tags=list(r["tags"] or []),
            similarity=float(r["similarity"]),
        )
        for r in rows
    ]


async def find_neighbor_ids(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    seed_id: UUID,
    *,
    threshold: float,
    limit: int = _MAX_NEIGHBORS_PER_QUERY,
) -> list[UUID]:
    """Cluster-build internal: return IDs of memories near a seed.

    Uses a self-join on the embedding column so we don't have to round-trip
    the seed's vector to Python. Returns IDs of memories whose embedding is
    cosine-similar to the seed's embedding, above threshold, EXCLUDING the
    seed itself.
    """
    rows = await conn.fetch(
        """
        WITH seed AS (
            SELECT embedding FROM memories
            WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL AND is_current = true
              AND embedding IS NOT NULL
        )
        SELECT m.id
        FROM memories m, seed
        WHERE m.tenant_id = $2
          AND m.id <> $1
          AND m.deleted_at IS NULL
          AND m.is_current = true
          AND m.embedding IS NOT NULL
          AND 1 - (m.embedding <=> seed.embedding) >= $3
        ORDER BY m.embedding <=> seed.embedding
        LIMIT $4
        """,
        seed_id,
        tenant_id,
        threshold,
        limit,
    )
    return [r["id"] for r in rows]


async def build_clusters(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[list[UUID]]:
    """Walk the corpus, BFS the similarity edge-graph, return clusters.

    Each returned cluster is a list of memory_ids forming a connected
    component in the "cosine_sim >= threshold" graph, with size >= 2.
    Singletons (memories with no above-threshold neighbors) are dropped.

    Algorithm: greedy BFS from each unvisited memory. Cost dominated by
    pgvector queries — with HNSW index, ~10ms each. For a 10k-memory tenant
    where most memories have no near-duplicates, this is ~100s. For a
    tenant with lots of redundancy the cost is dominated by the larger
    clusters' frontier expansion, still bounded.
    """
    # Get every current, non-deleted memory id. Order doesn't matter — BFS
    # explores neighbors regardless of start order.
    all_ids_rows = await conn.fetch(
        """
        SELECT id FROM memories
        WHERE tenant_id = $1 AND deleted_at IS NULL AND is_current = true
        """,
        tenant_id,
    )
    all_ids: list[UUID] = [r["id"] for r in all_ids_rows]

    visited: set[UUID] = set()
    clusters: list[list[UUID]] = []

    for start in all_ids:
        if start in visited:
            continue
        # BFS from `start`. Members accumulate as the frontier expands.
        cluster: set[UUID] = {start}
        frontier: list[UUID] = [start]
        while frontier:
            current = frontier.pop()
            if current in visited:
                continue
            visited.add(current)
            neighbors = await find_neighbor_ids(conn, tenant_id, current, threshold=threshold)
            for nid in neighbors:
                if nid not in cluster:
                    cluster.add(nid)
                    frontier.append(nid)
        if len(cluster) >= 2:
            clusters.append(sorted(cluster, key=str))
    return clusters
