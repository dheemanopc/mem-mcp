"""Chunk-build worker — build memory_chunks rows for chunk-level retrieval.

Runs every few minutes via systemd timer (chunks are eventually consistent
with writes; memory_search_chunks documents the lag). Selects memories
whose chunks are missing or stale (source_content_hash no longer matches
memories.content_hash — i.e. new memories and content updates), chunks the
content deterministically, embeds each chunk, and replaces the memory's
chunk set in one transaction.

Cost control:
- Single-chunk memories (the overwhelming majority) copy the memory's own
  embedding in SQL — zero extra embed calls.
- Multi-chunk memories embed each chunk with bounded concurrency.
- Any embed failure skips the memory (left stale; retried next pass).
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import TYPE_CHECKING, Any

from mem_mcp.config import get_settings
from mem_mcp.logging_setup import get_logger, setup_logging
from mem_mcp.memory.chunking import split_into_chunks

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

_log = get_logger("mem_mcp.jobs.chunk_build")

BATCH_SIZE = int(os.getenv("MEM_MCP_CHUNK_BUILD_BATCH_SIZE", "50"))
MAX_CONCURRENCY = int(os.getenv("MEM_MCP_CHUNK_BUILD_CONCURRENCY", "4"))

# Memories needing (re)build: indexable + live + current, and no chunk set
# built from the current content_hash. Covers both brand-new memories and
# content updates (memory_update bumps content_hash → old chunks go stale).
_CANDIDATES_SQL = """
SELECT m.id, m.content, m.content_hash, m.tenant_id,
       (m.embedding IS NOT NULL) AS has_embedding
FROM memories m
WHERE m.deleted_at IS NULL
  AND m.is_current = true
  AND m.indexable = true
  AND (m.expires_at IS NULL OR m.expires_at > NOW())
  AND NOT EXISTS (
      SELECT 1 FROM memory_chunks c
      WHERE c.memory_id = m.id AND c.source_content_hash = m.content_hash
  )
ORDER BY m.created_at ASC
LIMIT $1
"""

# Single-chunk fast path: copy the memory's own embedding without it ever
# crossing into Python (no vector codec needed, no embed call).
_INSERT_SINGLE_CHUNK_SQL = """
INSERT INTO memory_chunks (memory_id, tenant_id, chunk_index, content, embedding, source_content_hash)
SELECT id, tenant_id, 0, $2, embedding, content_hash
FROM memories WHERE id = $1
"""

_INSERT_CHUNK_SQL = """
INSERT INTO memory_chunks (memory_id, tenant_id, chunk_index, content, embedding, source_content_hash)
VALUES ($1, $2, $3, $4, $5::vector, $6)
"""

_DELETE_CHUNKS_SQL = "DELETE FROM memory_chunks WHERE memory_id = $1"


async def fetch_candidates(conn: asyncpg.Connection, batch_size: int) -> list[dict[str, Any]]:
    rows = await conn.fetch(_CANDIDATES_SQL, batch_size)
    return [dict(r) for r in rows]


async def build_chunks_for_memory(
    pool: asyncpg.Pool,
    embedder: Any,
    row: dict[str, Any],
) -> str:
    """Build the chunk set for one memory. Returns a tally key."""
    chunks = split_into_chunks(row["content"])
    if not chunks:
        return "skipped_empty"

    single_reuse = len(chunks) == 1 and row["has_embedding"]

    embeddings: list[list[float]] = []
    if not single_reuse:
        try:
            for chunk in chunks:
                emb = await embedder.embed(chunk)
                embeddings.append(emb.vector)
        except Exception as exc:
            _log.warning(
                "chunk_embed_failed",
                extra={"memory_id": str(row["id"]), "error": str(exc)[:200]},
            )
            return "failed_embed"

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_DELETE_CHUNKS_SQL, row["id"])
            if single_reuse:
                await conn.execute(_INSERT_SINGLE_CHUNK_SQL, row["id"], chunks[0])
            else:
                for i, (chunk, vec) in enumerate(zip(chunks, embeddings, strict=True)):
                    await conn.execute(
                        _INSERT_CHUNK_SQL,
                        row["id"],
                        row["tenant_id"],
                        i,
                        chunk,
                        vec,
                        row["content_hash"],
                    )
    return "built"


async def run_chunk_build(
    pool: asyncpg.Pool,
    embedder: Any,
    *,
    batch_size: int = BATCH_SIZE,
    max_concurrency: int = MAX_CONCURRENCY,
) -> dict[str, int]:
    """One pass: fetch candidates, build chunk sets with bounded concurrency."""
    async with pool.acquire() as conn:
        candidates = await fetch_candidates(conn, batch_size)

    tally: dict[str, int] = {
        "processed": len(candidates),
        "built": 0,
        "skipped_empty": 0,
        "failed_embed": 0,
        "failed_unknown": 0,
    }
    sem = asyncio.Semaphore(max_concurrency)

    async def _one(row: dict[str, Any]) -> str:
        async with sem:
            try:
                return await build_chunks_for_memory(pool, embedder, row)
            except Exception as exc:
                _log.warning(
                    "chunk_build_unexpected_error",
                    extra={"memory_id": str(row["id"]), "error": str(exc)[:200]},
                )
                return "failed_unknown"

    results = await asyncio.gather(*[_one(r) for r in candidates])
    for result in results:
        if result in tally:
            tally[result] += 1

    _log.info("chunk_build_complete", extra=tally)
    return tally


async def main(dry_run: bool = False) -> int:
    """Entry point: connect to DB, run one chunk-build pass, exit."""
    setup_logging(get_settings().log_level)
    settings = get_settings()

    import asyncpg

    pool = await asyncpg.create_pool(
        dsn=settings.db_maint_dsn_asyncpg,
        min_size=1,
        max_size=2,
        command_timeout=60,
        server_settings={"application_name": "mem-mcp-chunk-build"},
    )

    try:
        if dry_run:
            async with pool.acquire() as conn:
                candidates = await fetch_candidates(conn, BATCH_SIZE)
            _log.info("chunk_build_dry_run", extra={"candidates": len(candidates)})
            return 0
        # Same provider as the inline write path — mixing embedding providers
        # in one vector column corrupts similarity silently.
        from mem_mcp.embeddings.factory import make_embedding_client

        embedder = make_embedding_client(settings)
        result = await run_chunk_build(pool, embedder)
        _log.info("chunk_build_result", extra=result)
        return 0
    except Exception as exc:
        _log.error("chunk_build_failed", extra={"error": str(exc)[:500]})
        return 1
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
