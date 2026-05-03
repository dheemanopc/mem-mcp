"""Tests for connection pool isolation (T-6.6, spec S-4).

Verifies that the SET LOCAL app.current_tenant_id setting does not leak
across connection pool acquisitions. Each connection acquired from the pool
for a different tenant must see its own context, not the previous tenant's.

This is critical for a multi-tenant system using a shared pool with
SET LOCAL (which is transaction-scoped in Postgres).
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest


@pytest.mark.security
@pytest.mark.live_aws
async def test_pool_does_not_leak_tenant_context(pg_pool: Any) -> None:
    """Each acquisition from the pool sees its own tenant context.

    When tenant_tx(pool, tid_1) releases its connection, the setting
    must be cleared (because SET LOCAL is transaction-scoped). The next
    tenant_tx(pool, tid_2) must see tid_2, not tid_1.
    """
    from mem_mcp.db import tenant_tx

    results: dict[UUID, UUID] = {}

    async def use_tenant(tid: UUID) -> None:
        async with tenant_tx(pg_pool, tid) as conn:
            cur = await conn.fetchval("SELECT current_setting('app.current_tenant_id', true)")
            assert cur == str(tid), f"Tenant context mismatch: expected {tid}, got {cur}"
            results[tid] = UUID(cur)

    tids = [uuid4() for _ in range(10)]
    await asyncio.gather(*(use_tenant(t) for t in tids))

    # All results must match requested tenant IDs
    for tid in tids:
        assert results[tid] == tid, f"Tenant {tid} saw wrong context: {results[tid]}"


@pytest.mark.security
@pytest.mark.live_aws
async def test_pool_isolation_under_concurrent_load(pg_pool: Any) -> None:
    """High concurrency: 50 concurrent tenant_tx blocks.

    If SET LOCAL is not properly transaction-scoped, or if connections are
    reused incorrectly, this should expose context leaks under load.
    """
    from mem_mcp.db import tenant_tx

    results: list[UUID] = []
    errors: list[Exception] = []

    async def worker(tid: UUID) -> None:
        try:
            async with tenant_tx(pg_pool, tid) as conn:
                cur = await conn.fetchval("SELECT current_setting('app.current_tenant_id', true)")
                if cur == str(tid):
                    results.append(tid)
                else:
                    errors.append(
                        AssertionError(f"Tenant {tid} saw context {cur} (isolation broken)")
                    )
        except Exception as exc:
            errors.append(exc)

    tids = [uuid4() for _ in range(50)]
    await asyncio.gather(*(worker(t) for t in tids))

    assert len(errors) == 0, f"Isolation errors under concurrency: {errors}"
    assert sorted(results) == sorted(tids), (
        f"Not all tenants completed successfully. " f"Expected {len(tids)}, got {len(results)}"
    )


@pytest.mark.security
@pytest.mark.live_aws
async def test_pool_connection_reuse_clears_context(pg_pool: Any) -> None:
    """When a connection is returned to the pool, its SET LOCAL is cleared.

    This test explicitly acquires two connections sequentially from the same
    pool and verifies that the second one does not see the first's context.
    (This is more of a Postgres contract test than a mem-mcp-specific test.)
    """
    from mem_mcp.db import tenant_tx

    tid1 = uuid4()
    tid2 = uuid4()

    # First acquisition
    async with tenant_tx(pg_pool, tid1) as conn1:
        cur1 = await conn1.fetchval("SELECT current_setting('app.current_tenant_id', true)")
        assert cur1 == str(tid1)

    # Second acquisition (may or may not reuse conn1)
    async with tenant_tx(pg_pool, tid2) as conn2:
        cur2 = await conn2.fetchval("SELECT current_setting('app.current_tenant_id', true)")
        assert cur2 == str(
            tid2
        ), f"Pool leaked context from first tenant: expected {tid2}, got {cur2}"
