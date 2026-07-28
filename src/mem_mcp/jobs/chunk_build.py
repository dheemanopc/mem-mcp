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
from mem_mcp.embeddings.bedrock import EmbeddingError
from mem_mcp.logging_setup import get_logger, setup_logging
from mem_mcp.memory.chunking import split_into_chunks

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

_log = get_logger("mem_mcp.jobs.chunk_build")

BATCH_SIZE = int(os.getenv("MEM_MCP_CHUNK_BUILD_BATCH_SIZE", "50"))
MAX_CONCURRENCY = int(os.getenv("MEM_MCP_CHUNK_BUILD_CONCURRENCY", "4"))
# After this many failed passes a memory is marked terminal and drops out of the
# candidate set for good (until its content changes). Guards against a permanently
# un-embeddable memory wedging the oldest-first queue. invalid_input terminalizes
# immediately regardless of this cap.
MAX_ATTEMPTS = int(os.getenv("MEM_MCP_CHUNK_BUILD_MAX_ATTEMPTS", "8"))

# Memories needing (re)build: indexable + live + current, and no chunk set
# built from the current content_hash. Covers both brand-new memories and
# content updates (memory_update bumps content_hash → old chunks go stale).
#
# The LEFT JOIN to chunk_build_state excludes memories that are either
# terminally failed or still inside their exponential-backoff window, so a
# memory that cannot be embedded stops being re-selected every pass (the
# runaway fixed in migration 0039). New/never-attempted memories have no state
# row (s.memory_id IS NULL) and are always eligible.
_CANDIDATES_SQL = """
SELECT m.id, m.content, m.content_hash, m.tenant_id,
       (m.embedding IS NOT NULL) AS has_embedding
FROM memories m
LEFT JOIN chunk_build_state s ON s.memory_id = m.id
WHERE m.deleted_at IS NULL
  AND m.is_current = true
  AND m.indexable = true
  AND (m.expires_at IS NULL OR m.expires_at > NOW())
  AND NOT EXISTS (
      SELECT 1 FROM memory_chunks c
      WHERE c.memory_id = m.id AND c.source_content_hash = m.content_hash
  )
  AND (s.memory_id IS NULL OR (s.terminal = false AND s.next_attempt_at <= NOW()))
ORDER BY m.created_at ASC
LIMIT $1
"""

# Record a failed chunk-build attempt with exponential backoff. terminal is set
# when the error is permanent ($2) or the attempt count reaches MAX_ATTEMPTS ($3).
# Backoff is computed SQL-side off the post-increment attempt count (5min *
# 2^(attempts-1), capped at 6h) so we never have to read the row first.
_RECORD_FAILURE_SQL = """
INSERT INTO chunk_build_state AS s (memory_id, attempts, next_attempt_at, terminal, last_error, updated_at)
VALUES ($1, 1, NOW() + interval '5 minutes', ($2 OR 1 >= $3), $4, NOW())
ON CONFLICT (memory_id) DO UPDATE SET
    attempts = s.attempts + 1,
    next_attempt_at = NOW() + LEAST(interval '5 minutes' * power(2, LEAST(s.attempts, 7)), interval '6 hours'),
    terminal = ($2 OR (s.attempts + 1) >= $3),
    last_error = $4,
    updated_at = NOW()
"""

# On a successful build, drop any prior failure/backoff state for the memory.
_CLEAR_STATE_SQL = "DELETE FROM chunk_build_state WHERE memory_id = $1"

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


async def record_failure(
    pool: asyncpg.Pool, memory_id: Any, *, permanent: bool, error: str
) -> None:
    """Persist a failed chunk-build attempt so the memory backs off / terminalizes
    instead of being re-selected every pass. Best-effort: never raises to caller."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(_RECORD_FAILURE_SQL, memory_id, permanent, MAX_ATTEMPTS, error[:500])
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning(
            "chunk_build_state_record_failed",
            extra={"memory_id": str(memory_id), "error": str(exc)[:200]},
        )


async def build_chunks_for_memory(
    pool: asyncpg.Pool,
    embedder: Any,
    row: dict[str, Any],
) -> str:
    """Build the chunk set for one memory. Returns a tally key.

    On embed failure, records backoff/terminal state (migration 0039) so the
    memory is not re-embedded on the next pass; on success, clears that state.
    """
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
        except EmbeddingError as exc:
            # invalid_input is permanent (bad content / out-of-range) → terminalize
            # now; throttled/unavailable are transient → exponential backoff.
            permanent = exc.code == "invalid_input"
            await record_failure(pool, row["id"], permanent=permanent, error=f"{exc.code}: {exc}")
            _log.warning(
                "chunk_embed_failed",
                extra={"memory_id": str(row["id"]), "code": exc.code, "permanent": permanent},
            )
            return "failed_embed"
        except Exception as exc:
            # Unexpected (e.g. transient DB/embedder error) — back off, don't wedge.
            await record_failure(pool, row["id"], permanent=False, error=f"unknown: {exc}")
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
            await conn.execute(_CLEAR_STATE_SQL, row["id"])
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
                # Back off on any unexpected error too, so a persistently-failing
                # memory can never wedge the oldest-first candidate queue.
                await record_failure(pool, row["id"], permanent=False, error=f"unexpected: {exc}")
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

    # init=_init_connection registers the pgvector text codec — WITHOUT it,
    # asyncpg rejects a Python list passed to a `vector` column with
    # "expected str, got list", so every multi-chunk INSERT fails (only the
    # single-chunk SQL-to-SQL copy path worked). This standalone job pool must
    # register the same codecs the app pool does.
    from mem_mcp.db.pool import _init_connection

    pool = await asyncpg.create_pool(
        dsn=settings.db_maint_dsn_asyncpg,
        min_size=1,
        max_size=2,
        command_timeout=60,
        server_settings={"application_name": "mem-mcp-chunk-build"},
        init=_init_connection,
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
