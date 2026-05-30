"""Long-running drain daemon for the async_write_queue.

Per blessed Decision 2 in proposal memsys:80b04691 (ratified via
memsys:26610ba6): Type=simple daemon with internal sleep(1.0) loop —
NOT a 1-second oneshot systemd timer. At 1s cadence the timer/fork/init
overhead exceeds the actual drain work; a long-running process pays
init once and runs continuously.

Each iteration:
  1. system_tx: atomically claim ≤50 oldest queued rows via
     SELECT ... FOR UPDATE SKIP LOCKED + UPDATE state='in_flight'.
  2. For each claimed row: tenant_tx for the row's tenant_id; run the
     full memory_write logic with the queued payload; UPDATE the queue
     row to succeeded (with result_memory_id) or failed (with
     error_code + error_message + notice queued to caller).
  3. sleep(1.0).

Blessed Decision 1: the resulting memory carries
created_at = submitted_at (queued-at), NOT drain-time. Implemented
via post-INSERT UPDATE on memories.created_at within the same tenant_tx
so the transient state isn't visible.

Blessed Decision 3: drain runs as mem_app (the same role memory_write
runs under). The async_write_queue UPDATE policy in migration 0028
allows mem_app to UPDATE both with tenant_id matching the GUC and with
empty GUC (the claim-step case).

Blessed Decision 5: single-attempt v1. attempts column is incremented
once on claim; no retry-with-backoff. Failure surfaces via notice.

Usage: `python -m mem_mcp.jobs.async_write_drain` (mostly invoked by
deploy/systemd/mem-mcp-async-write-drain.service).
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from typing import Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]

from mem_mcp.db import close_pool, init_pool, system_tx, tenant_tx
from mem_mcp.plugins.clients.notices import NoticeClientImpl

log = logging.getLogger(__name__)

DRAIN_BATCH_SIZE = 50
DRAIN_SLEEP_SECONDS = 1.0


async def _claim_batch(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Atomically transition up to DRAIN_BATCH_SIZE queued rows → in_flight.

    Cross-tenant SELECT (drain doesn't have a single tenant). Per-tenant
    ordering preserved by ORDER BY (tenant_id, submitted_at). Returns rows
    with (request_id, tenant_id, payload, submitted_at) for processing.
    """
    async with system_tx(pool) as conn:
        rows = await conn.fetch(
            """
            UPDATE async_write_queue
               SET state = 'in_flight',
                   attempts = attempts + 1,
                   last_attempted_at = now()
             WHERE request_id IN (
                SELECT request_id FROM async_write_queue
                 WHERE state = 'queued'
                 ORDER BY tenant_id, submitted_at ASC
                 LIMIT $1
                 FOR UPDATE SKIP LOCKED
             )
            RETURNING request_id, tenant_id, payload, submitted_at
            """,
            DRAIN_BATCH_SIZE,
        )
    return [dict(r) for r in rows]


async def _process_one(pool: asyncpg.Pool, row: dict[str, Any]) -> None:
    """Run the full memory_write logic for one claimed queue row.

    Mirrors the synchronous tool's flow but synthesizes the ToolContext
    locally (no MCP request, no audit caller chain). The result memory's
    created_at is overridden to submitted_at via an UPDATE inside the
    same tenant_tx, satisfying blessed Decision 1.
    """
    request_id: UUID = row["request_id"]
    tenant_id: UUID = row["tenant_id"]
    submitted_at = row["submitted_at"]
    raw_payload = row["payload"]
    payload: dict[str, Any] = (
        json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    )

    try:
        result_memory_id = await _execute_memory_write(pool, tenant_id, payload, submitted_at)
        await _mark_succeeded(pool, request_id, result_memory_id)
        log.info(
            "async_write_succeeded",
            extra={
                "request_id": str(request_id),
                "tenant_id": str(tenant_id),
                "memory_id": str(result_memory_id),
            },
        )
    except Exception as exc:
        log.exception(
            "async_write_drain_failed",
            extra={"request_id": str(request_id), "tenant_id": str(tenant_id)},
        )
        await _mark_failed_and_notify(pool, request_id, tenant_id, exc)


async def _execute_memory_write(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    payload: dict[str, Any],
    submitted_at: Any,
) -> UUID:
    """Run memory_write tool logic against a tenant_tx then override created_at.

    Uses the existing MemoryWriteTool path so all the drain-deferred
    validation (embedding, full ref access-check, dedup, slug allocation,
    parent/supersedes validation) happens here at drain time, exactly as
    the sync tool would.
    """
    from mem_mcp.config import get_settings
    from mem_mcp.embeddings.bedrock import BedrockEmbeddingClient
    from mem_mcp.mcp.tools._base import ToolContext
    from mem_mcp.mcp.tools._deps import make_default_deps
    from mem_mcp.mcp.tools.write import MemoryWriteInput, MemoryWriteTool

    inp = MemoryWriteInput.model_validate(payload)
    s = get_settings()
    embeddings = BedrockEmbeddingClient(region=s.region, model_id=s.bedrock_model_id)
    deps = make_default_deps(embeddings=embeddings)
    ctx = ToolContext(
        request_id=f"async-drain-{tenant_id}",
        tenant_id=tenant_id,
        identity_id=tenant_id,  # drain has no separate identity; use tenant_id
        client_id="async-drain",
        scopes=frozenset({"memory.write"}),
        db_pool=pool,
        deps=deps,
    )
    tool = MemoryWriteTool()
    out = await tool(ctx, inp)
    memory_id: UUID = out.id  # type: ignore[attr-defined]

    # Override created_at = submitted_at per blessed Decision 1. Done in a
    # separate tenant_tx — the previous tool invocation already committed.
    # Per-tenant ordering on downstream `ORDER BY created_at` queries needs
    # this to match submission order even when multiple rows drain in the
    # same batch.
    async with tenant_tx(pool, tenant_id) as conn:
        await conn.execute(
            "UPDATE memories SET created_at = $1 WHERE id = $2 AND tenant_id = $3",
            submitted_at,
            memory_id,
            tenant_id,
        )
    return memory_id


async def _mark_succeeded(pool: asyncpg.Pool, request_id: UUID, memory_id: UUID) -> None:
    async with system_tx(pool) as conn:
        await conn.execute(
            """
            UPDATE async_write_queue
               SET state = 'succeeded',
                   completed_at = now(),
                   result_memory_id = $2
             WHERE request_id = $1
            """,
            request_id,
            memory_id,
        )


async def _mark_failed_and_notify(
    pool: asyncpg.Pool, request_id: UUID, tenant_id: UUID, exc: Exception
) -> None:
    error_code = type(exc).__name__
    error_message = str(exc)[:500]
    async with system_tx(pool) as conn:
        await conn.execute(
            """
            UPDATE async_write_queue
               SET state = 'failed',
                   completed_at = now(),
                   error_code = $2,
                   error_message = $3
             WHERE request_id = $1
            """,
            request_id,
            error_code,
            error_message,
        )

    # Surface via NoticeClient — reuses the reminders-plugin notice infra
    # (memsys:b72140b6). User sees the failure on their next MCP call.
    notice = NoticeClientImpl(pool, tenant_id, "async_write")
    try:
        await notice.queue(
            kind="async_write.failed",
            template=("Async write {request_id} failed: {error_code} — {error_message}"),
            payload={
                "request_id": str(request_id),
                "error_code": error_code,
                "error_message": error_message,
            },
        )
    except Exception:
        log.exception(
            "async_write_notice_queue_failed",
            extra={"request_id": str(request_id), "tenant_id": str(tenant_id)},
        )


async def drain_once(pool: asyncpg.Pool) -> int:
    """Single drain iteration. Returns the number of rows processed."""
    rows = await _claim_batch(pool)
    for row in rows:
        await _process_one(pool, row)
    return len(rows)


async def main() -> int:
    """Long-running entrypoint. Loops drain_once + sleep until SIGTERM."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log.info("async_write_drain_starting")

    stop = asyncio.Event()

    def _shutdown(*_args: Any) -> None:
        log.info("async_write_drain_shutdown_signal")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass  # Windows / restricted envs; SIGTERM still kills.

    pool = await init_pool()
    log.info("async_write_drain_pool_ready")

    try:
        while not stop.is_set():
            try:
                n = await drain_once(pool)
                if n > 0:
                    log.info("async_write_drain_iteration", extra={"processed": n})
            except Exception:
                log.exception("async_write_drain_iteration_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=DRAIN_SLEEP_SECONDS)
            except TimeoutError:
                pass
    finally:
        await close_pool()
        log.info("async_write_drain_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
