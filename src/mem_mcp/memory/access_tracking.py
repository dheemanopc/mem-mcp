"""Throttled access-time bump for memory_get / memory_search.

Every memory_get and every memory_search RESULT bumps last_accessed_at on
the affected row(s) so the user can find "stale" memories (not read in N
days) via the management UI.

Design (per session 2026-05-19 discussion):

- Throttle: only UPDATE if last_accessed_at < now() - interval '5 minutes'.
  A repeat read in 30s is a no-op. Trades exact access_count for an
  approximate one — fine since we only need staleness signal.
- Batch: search returning N hits issues ONE UPDATE WHERE id = ANY($ids),
  not N separate UPDATEs.
- Fire-and-forget: the bump runs in a detached asyncio task so the read
  doesn't block on it. Failures are logged, not raised — the read's
  result is the source of truth.
- No index on last_accessed_at: keeps Postgres HOT updates working so
  write bloat is bounded. See migration 0009 for the rationale.

Used internally by mem_mcp.mcp.tools.get and mem_mcp.mcp.tools.search. Not
exposed via MCP.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import TYPE_CHECKING
from uuid import UUID

from mem_mcp.db import tenant_tx
from mem_mcp.logging_setup import get_logger

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


_log = get_logger("mem_mcp.memory.access_tracking")

# Skip the UPDATE if a memory was already touched within this window.
# Picked at 5 minutes — short enough to keep "last access" meaningful for
# stale-detection (which operates on days), long enough to absorb repeat-reads
# during a single LLM session without write amplification.
ACCESS_BUMP_THROTTLE_SECONDS = 300

# Hold strong refs to in-flight bump tasks so asyncio's GC doesn't reap them
# mid-flight (per Python docs warning on create_task — orphan refs are a real
# silent-drop risk under load).
_inflight_tasks: set[asyncio.Task[None]] = set()


async def bump_access(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    memory_ids: Iterable[UUID],
) -> None:
    """Schedule a throttled access-time bump for the given memory IDs.

    Fire-and-forget — returns immediately. The actual UPDATE happens on a
    background asyncio task with its own connection acquire. Exceptions are
    swallowed (logged) so a transient DB hiccup never breaks a read.
    """
    ids = [i for i in memory_ids if i is not None]
    if not ids:
        return
    task = asyncio.create_task(_do_bump(pool, tenant_id, ids))
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)


async def _do_bump(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    memory_ids: list[UUID],
) -> None:
    try:
        async with tenant_tx(pool, tenant_id) as conn:
            # Single batched UPDATE; throttle clause means repeated calls
            # within the window are no-ops at the SQL layer.
            await conn.execute(
                f"""
                UPDATE memories
                SET last_accessed_at = now(),
                    access_count = access_count + 1
                WHERE id = ANY($1::uuid[])
                  AND (
                    last_accessed_at IS NULL
                    OR last_accessed_at < now() - interval '{ACCESS_BUMP_THROTTLE_SECONDS} seconds'
                  )
                """,
                memory_ids,
            )
    except Exception as exc:
        _log.warning(
            "access_bump_failed",
            tenant_id=str(tenant_id),
            count=len(memory_ids),
            exc_type=type(exc).__name__,
            exc_message=str(exc)[:200],
        )
