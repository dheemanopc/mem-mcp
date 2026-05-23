"""Embedding backfill worker — retry failed embeddings from Bedrock.

Runs hourly via systemd timer. Selects up to batch_size candidates with
failed_throttled / failed_unavailable / failed_unknown status, attempts to
embed them with bounded concurrency, updates embedding + embedding_status='ok'
on success, leaves status unchanged on failure (retryable on next pass).
Validation errors (invalid_input) are marked as failed_validation and NOT
retried.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import TYPE_CHECKING, Any

from mem_mcp.config import get_settings
from mem_mcp.embeddings.bedrock import BedrockEmbeddingClient, EmbeddingError
from mem_mcp.logging_setup import get_logger, setup_logging

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

_log = get_logger("mem_mcp.jobs.embedding_backfill")

# Environment configuration
BATCH_SIZE = int(os.getenv("MEM_MCP_EMBEDDING_BACKFILL_BATCH_SIZE", "50"))
MAX_CONCURRENCY = int(os.getenv("MEM_MCP_EMBEDDING_BACKFILL_CONCURRENCY", "10"))


async def fetch_candidates(conn: asyncpg.Connection, batch_size: int) -> list[dict[str, str]]:
    """Fetch up to batch_size candidate rows for embedding retry.

    Only selects:
    - embedding IS NULL (no embedding yet)
    - embedding_status IN (failed_throttled, failed_unavailable, failed_unknown)
    - content length <= 32000 (Titan v2 cap)
    - deleted_at IS NULL (active memories only)
    - expires_at IS NULL OR expires_at > NOW (not already expired)

    Ordered by created_at ASC (oldest first).
    """
    rows = await conn.fetch(
        """
        SELECT id, content
        FROM memories
        WHERE embedding IS NULL
          AND embedding_status IN ('failed_throttled', 'failed_unavailable', 'failed_unknown')
          AND length(content) <= 32000
          AND deleted_at IS NULL
          AND (expires_at IS NULL OR expires_at > NOW())
        ORDER BY created_at ASC
        LIMIT $1
        """,
        batch_size,
    )
    return [{"id": str(r["id"]), "content": r["content"]} for r in rows]


async def update_row_success(
    conn: asyncpg.Connection, memory_id: str, embedding_vec: list[float]
) -> None:
    """Update row: set embedding vector and embedding_status='ok'."""
    await conn.execute(
        """
        UPDATE memories
        SET embedding = $1::vector,
            embedding_status = 'ok'
        WHERE id = $2::uuid
        """,
        embedding_vec,
        memory_id,
    )


async def update_row_status(conn: asyncpg.Connection, memory_id: str, status: str) -> None:
    """Update row: set embedding_status (for failed_validation, etc)."""
    await conn.execute(
        """
        UPDATE memories
        SET embedding_status = $1
        WHERE id = $2::uuid
        """,
        status,
        memory_id,
    )


async def run_backfill(
    pool: asyncpg.Pool,
    embedder: Any,
    *,
    batch_size: int = BATCH_SIZE,
    max_concurrency: int = MAX_CONCURRENCY,
) -> dict[str, int]:
    """Run one-shot backfill pass.

    Fetches candidates, embeds with bounded concurrency, updates rows.
    Returns tally of {processed, succeeded, failed_throttled, failed_unavailable,
    failed_validation, failed_unknown}.
    """
    async with pool.acquire() as conn:
        candidates = await fetch_candidates(conn, batch_size)

    tally: dict[str, int] = {
        "processed": len(candidates),
        "succeeded": 0,
        "failed_throttled": 0,
        "failed_unavailable": 0,
        "failed_validation": 0,
        "failed_unknown": 0,
    }

    sem = asyncio.Semaphore(max_concurrency)

    async def _embed_one(row: dict[str, Any]) -> str:
        """Embed one row, return result status."""
        async with sem:
            try:
                emb = await embedder.embed(row["content"])
                async with pool.acquire() as conn:
                    await update_row_success(conn, row["id"], emb.vector)
                return "ok"
            except EmbeddingError as e:
                status = f"failed_{e.code}"
                if e.code == "invalid_input":
                    # Mark validation errors as non-retryable
                    async with pool.acquire() as conn:
                        await update_row_status(conn, row["id"], "failed_validation")
                    status = "failed_validation"
                # else: throttled/unavailable/unknown — leave status unchanged for retry
                return status
            except Exception as ex:
                _log.warning(
                    "embedding_backfill_unexpected_error",
                    extra={"memory_id": row["id"], "error": str(ex)},
                )
                return "failed_unknown"

    results = await asyncio.gather(*[_embed_one(r) for r in candidates], return_exceptions=True)

    # Tally results
    for result in results:
        if isinstance(result, str):
            if result == "ok":
                tally["succeeded"] += 1
            elif result in tally:
                tally[result] += 1

    _log.info("embedding_backfill_complete", extra=tally)
    return tally


async def main(dry_run: bool = False) -> int:
    """Entry point: connect to DB, run backfill, exit."""
    setup_logging(get_settings().log_level)
    settings = get_settings()

    import asyncpg

    pool = await asyncpg.create_pool(
        dsn=settings.db_maint_dsn_asyncpg,
        min_size=1,
        max_size=2,
        command_timeout=60,
        server_settings={"application_name": "mem-mcp-embedding-backfill"},
    )

    try:
        region = os.getenv("AWS_REGION", "ap-south-1")
        embedder = BedrockEmbeddingClient(region=region)
        result = await run_backfill(pool, embedder)
        _log.info("backfill_result", extra=result)
        return 0
    except Exception as ex:
        _log.error("backfill_failed", extra={"error": str(ex)})
        return 1
    finally:
        await pool.close()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
