"""Tests for cross-tenant search isolation (T-6.3, spec S-1).

Verifies that Row-Level Security (RLS) policies prevent a tenant from
searching or viewing another tenant's memories. The test skeletons here
require live Postgres + live MCP endpoint to fully verify; they are
marked @pytest.mark.live_aws and skipped unless pytest is run with --live-aws.

Without a live MCP endpoint, the mcp_client fixture returns empty results
regardless of query, so these tests will trivially pass. Real verification
requires wiring the fixture to a live instance.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.security
@pytest.mark.live_aws
async def test_cross_tenant_search_isolation(setup_two_tenants: Any, mcp_client: Any) -> None:
    """Tenant B cannot search or see Tenant A's memories.

    Spec S-1: RLS policies must ensure cross-tenant isolation at the database
    level. When Tenant A writes a memory containing 'atlas pivot decision',
    Tenant B's search for 'atlas pivot' must return empty.

    Real verification requires the mcp_client fixture to be wired to a live
    MCP endpoint and a real Postgres instance with RLS enabled. Currently
    the stub returns empty regardless, so this test is a SKELETON; flip the
    live_aws marker AND swap mcp_client for a real client to actually verify.
    """
    a, b = setup_two_tenants.a, setup_two_tenants.b  # noqa: F841
    # Pending: wire to real MCP endpoint
    # await mcp_client.write(a.token, "secret atlas pivot decision")
    # res = await mcp_client.search(b.token, "atlas pivot")
    # assert res == [], "Tenant B saw Tenant A's memory — RLS isolation broken"


@pytest.mark.security
@pytest.mark.live_aws
async def test_cross_tenant_get_by_id(setup_two_tenants: Any, mcp_client: Any) -> None:
    """Tenant B cannot read Tenant A's memory by ID.

    Even if Tenant B somehow learns a memory ID from Tenant A, a direct
    memory.get call must be rejected.
    """
    a, b = setup_two_tenants.a, setup_two_tenants.b  # noqa: F841
    # Pending: wire to real MCP endpoint
    # mem_a = await mcp_client.write(a.token, "tenant A secret")
    # res = await mcp_client.get(b.token, memory_id=mem_a["id"])
    # assert res is None or res.error, "Tenant B read Tenant A's memory"


@pytest.mark.security
@pytest.mark.live_aws
async def test_cross_tenant_stats(setup_two_tenants: Any, mcp_client: Any) -> None:
    """Tenant B's stats endpoint only returns Tenant B's counts.

    The stats tool must not leak counts of other tenants' memories.
    """
    a, b = setup_two_tenants.a, setup_two_tenants.b  # noqa: F841
    # Pending: wire to real MCP endpoint
    # await mcp_client.write(a.token, "memory A")
    # stats_b = await mcp_client.stats(b.token)
    # assert stats_b["total_memories"] == 0, "Stats leaked Tenant A's count"
