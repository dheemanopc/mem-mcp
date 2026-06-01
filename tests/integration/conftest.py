"""Integration-test fixtures for Plugin SDK IT-08 + Tier-2 end-to-end tests.

Per Tier-2 Plan `cb47f907` + v2 delta `894cea7a` DELTA 5: extracted from the
inline-setup pattern in `test_references_integration.py::TestIt08OpaqueResolutionFailures`
so multiple IT-08-class tests (Tier-1 carry-forward + Tier-2 opaque-read +
Tier-2 happy-path) can DRY against one fixture.

All fixtures gated on `pg_pool` (which skips when MEM_MCP_TEST_DSN unset).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest

from mem_mcp.teams.assignments import assign_role


@dataclass(frozen=True)
class MultiTeamSetup:
    """Two tenants, two teams, one cross-team-accessible memory.

    - tenant_a + tenant_b — distinct user identities.
    - team_a + team_b — distinct teams; tenant_a in team_a only, tenant_b in team_b only.
    - memory_in_team_a — visibility='team', team_id=team_a; reachable by tenant_a only.
    - memory_in_team_b — visibility='team', team_id=team_b; reachable by tenant_b only.

    Test pattern: tenant_a calls SDK on memory_in_team_b → IT-08 opaque error/None.
    """

    tenant_a: UUID
    tenant_b: UUID
    team_a: UUID
    team_b: UUID
    memory_in_team_a: UUID
    memory_in_team_b: UUID


@pytest.fixture
async def multi_tenant_team_setup(pg_pool: Any) -> AsyncIterator[MultiTeamSetup]:
    """Build a two-tenant + two-team setup with one memory per team.

    Yields the IDs; caller uses `MemoryClientImpl` with `tenant_id=tenant_a` /
    `tenant_id=tenant_b` to exercise SDK paths against the cross-team boundary.

    Idempotent on test teardown (relies on PG ON DELETE CASCADE for tenants).
    """
    setup_data: MultiTeamSetup | None = None
    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            tenant_a = await conn.fetchval(
                "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                f"it08-a-{uuid4()}@example.test",
            )
            tenant_b = await conn.fetchval(
                "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                f"it08-b-{uuid4()}@example.test",
            )

            team_a = await conn.fetchval(
                "INSERT INTO teams (name, created_by_tenant_id) "
                "VALUES ($1, $2) RETURNING id",
                f"it08-team-a-{uuid4()}",
                tenant_a,
            )
            team_b = await conn.fetchval(
                "INSERT INTO teams (name, created_by_tenant_id) "
                "VALUES ($1, $2) RETURNING id",
                f"it08-team-b-{uuid4()}",
                tenant_b,
            )

            await assign_role(
                conn,
                parent_team_id=team_a,
                member_kind="user",
                member_id=tenant_a,
                role_key="member",
                assigned_by_user_id=tenant_a,
            )
            await assign_role(
                conn,
                parent_team_id=team_b,
                member_kind="user",
                member_id=tenant_b,
                role_key="member",
                assigned_by_user_id=tenant_b,
            )

            mem_a = await conn.fetchval(
                "INSERT INTO memories (tenant_id, team_id, content, content_hash, "
                "embedding, source_kind, type, visibility) "
                "VALUES ($1, $2, 'memory in team A', $3, "
                "array_fill(0::real, ARRAY[1024])::vector, 'api', 'note', 'team') "
                "RETURNING id",
                tenant_a,
                team_a,
                f"hash-it08-a-{uuid4().hex}",
            )
            mem_b = await conn.fetchval(
                "INSERT INTO memories (tenant_id, team_id, content, content_hash, "
                "embedding, source_kind, type, visibility) "
                "VALUES ($1, $2, 'memory in team B', $3, "
                "array_fill(0::real, ARRAY[1024])::vector, 'api', 'note', 'team') "
                "RETURNING id",
                tenant_b,
                team_b,
                f"hash-it08-b-{uuid4().hex}",
            )

            setup_data = MultiTeamSetup(
                tenant_a=tenant_a,
                tenant_b=tenant_b,
                team_a=team_a,
                team_b=team_b,
                memory_in_team_a=mem_a,
                memory_in_team_b=mem_b,
            )

    assert setup_data is not None
    try:
        yield setup_data
    finally:
        # Cleanup: cascade-delete tenants (cascades to teams, memories, role assignments)
        async with pg_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM tenants WHERE id = ANY($1::uuid[])",
                [setup_data.tenant_a, setup_data.tenant_b],
            )


@pytest.fixture
def sdk_client_factory(pg_pool: Any) -> Any:
    """Returns a callable that builds a MemoryClientImpl for a given tenant.

    Usage: `sdk = sdk_client_factory(tenant_id=setup.tenant_a)` →
    a `MemoryClientImpl` instance ready for `await sdk.write(...)` etc.

    Uses the real Noop quotas + audit; provides a stub embedding client so
    tests don't require Bedrock/AWS at run time. Write paths that need a
    real embedding can override deps per-test.
    """
    from mem_mcp.plugins.clients.memory import MemoryClientImpl

    def _factory(*, tenant_id: UUID, plugin_id: str = "it08-test") -> MemoryClientImpl:
        deps_stub = _build_minimal_deps()
        return MemoryClientImpl(
            pool=pg_pool,
            tenant_id=tenant_id,
            plugin_id=plugin_id,
            deps=deps_stub,
            identity_id=tenant_id,
        )

    return _factory


def _build_minimal_deps() -> Any:
    """ToolDeps with stub embedding (fake 1024-d zero vector) + noop audit/quotas."""
    from mem_mcp.audit.logger import NoopAuditLogger
    from mem_mcp.mcp.tools._deps import NoopQuotas, ToolDeps

    class _StubEmbed:
        async def embed(self, content: str) -> Any:
            from mem_mcp.embeddings.bedrock import EmbedResult

            return EmbedResult(vector=[0.0] * 1024, input_tokens=max(1, len(content) // 4))

    return ToolDeps(
        embeddings=_StubEmbed(),
        audit=NoopAuditLogger(),
        quotas=NoopQuotas(),
    )
