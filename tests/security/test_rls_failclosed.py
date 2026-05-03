"""Tests for RLS fail-closed behavior (T-6.5, spec S-3).

Verifies that Row-Level Security policies in Postgres are properly
configured to deny access by default if no tenant context is set.

A SELECT against the memories table without a tenant context (i.e.,
without SET LOCAL app.current_tenant_id) must return 0 rows, even if
data exists. This ensures that any code path that forgets to set tenant
context will see empty results, not leak data.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.security
@pytest.mark.live_aws
async def test_rls_failclosed_select_without_tenant_context(pg_pool: Any) -> None:
    """SELECT FROM memories without tenant context returns 0 rows (fail-closed).

    Spec S-3: RLS policies must prevent a connection from seeing any rows if
    the app.current_tenant_id setting is not set. This is the "fail-closed"
    principle — default deny.

    This test acquires a bare connection from the pool, does NOT set the
    tenant context, and verifies that even a simple SELECT returns empty.
    """
    async with pg_pool.acquire() as conn:
        # Fresh connection, no tenant context set
        rows = await conn.fetch("SELECT * FROM memories LIMIT 100")
    assert rows == [], (
        "RLS not fail-closed — SELECT without tenant context returned rows. "
        "RLS policies must deny access by default."
    )


@pytest.mark.security
@pytest.mark.live_aws
async def test_rls_failclosed_count_without_tenant_context(pg_pool: Any) -> None:
    """COUNT(*) on memories without tenant context returns 0.

    Even aggregation queries must respect RLS. A COUNT without tenant context
    should return 0 (or fail, but returning 0 is more robust).
    """
    async with pg_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM memories")
    assert count == 0, f"RLS not fail-closed — COUNT returned {count} without tenant context"


@pytest.mark.security
@pytest.mark.live_aws
async def test_rls_failclosed_with_tenant_context(pg_pool: Any) -> None:
    """For contrast: with tenant context SET, SELECT works (control case).

    This is not a security test per se, but verifies that the RLS policy
    is not broken — that legitimate queries (with tenant context) do work.
    """
    from uuid import uuid4

    from mem_mcp.db import tenant_tx

    tid = uuid4()
    # Use tenant_tx which sets the context
    async with tenant_tx(pg_pool, tid) as conn:
        # No need to actually insert data; just verify the context is set
        tenant_id_from_setting = await conn.fetchval(
            "SELECT current_setting('app.current_tenant_id', true)"
        )
    assert tenant_id_from_setting == str(
        tid
    ), f"Tenant context not set correctly: {tenant_id_from_setting} != {tid}"
