"""Integration tests for the references-validator same-tenant escape fix.

Per bug fab23ec5 + DA ratification f74663a6: the write-time references
validator and its sibling filter sites must allow same-tenant access
without requiring a UEA row (matching the read-path memories_select RLS
semantics).

Includes IT-RV-1 (end-to-end PMO DO repro via the SDK) and carve-out
regression-locks for Sites 5 (slug_lookup), 6 (get_batch slug-path),
and 8 (memory_get slug-path) per DA §B-3.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools.write import ReferenceInput
from mem_mcp.plugins.contract import PluginValidationError

pytestmark = pytest.mark.asyncio


async def _natural_path_setup(conn: Any) -> dict[str, Any]:
    """Create a tenant + personal team via raw SQL (mimicking migration 0026 path).

    BYPASSES assign_role/sync_refresh_on_user_assignment, so UEA is empty
    for the (tenant, team) pair. Asserts UEA emptiness as a fixture guard.
    """
    tenant = await conn.fetchval(
        "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
        f"natural-{uuid4()}@example.test",
    )
    team = await conn.fetchval(
        "INSERT INTO teams (name, created_by_tenant_id, team_type) "
        "VALUES ($1, $2, 'personal') RETURNING id",
        f"natural-{uuid4().hex[:8]}",
        tenant,
    )
    owner_role_id = await conn.fetchval(
        "SELECT id FROM roles_catalog WHERE role_key = 'owner' AND plugin_id IS NULL"
    )
    await conn.execute(
        """
        INSERT INTO team_role_assignments
            (parent_team_id, member_kind, member_id, role_id, status, assigned_by_user_id)
        VALUES ($1, 'user', $2, $3, 'active', $2)
        """,
        team,
        tenant,
        owner_role_id,
    )
    # tenant_identities row with default_team_id so the SDK can resolve team
    # automatically (mirrors what migration 0026 backfills).
    await conn.execute(
        """
        INSERT INTO tenant_identities
        (tenant_id, cognito_sub, provider, email, is_primary, default_team_id)
        VALUES ($1, $2, 'cognito', $3, true, $4)
        """,
        tenant,
        f"sub-natural-{tenant}",
        f"natural-{tenant}@example.test",
        team,
    )
    # oauth_clients row for SDK's synthetic client_id (per IT-08 fixture pattern).
    await conn.execute(
        """
        INSERT INTO oauth_clients
        (id, redirect_uris, scope, registration_payload, review_status)
        VALUES ($1, ARRAY[]::text[], 'memory.read memory.write',
                '{}'::jsonb, 'auto_allowed')
        ON CONFLICT (id) DO NOTHING
        """,
        "plugin:natural-path-test",
    )
    # Guard the fixture premise: UEA must be empty for (tenant, team).
    uea = await conn.fetchval(
        "SELECT 1 FROM user_effective_team_access " "WHERE user_id = $1 AND resource_team_id = $2",
        tenant,
        team,
    )
    assert uea is None, "natural-path fixture should NOT have populated UEA"
    return {"tenant": tenant, "team": team}


async def _cleanup_natural(pool: Any, tenant: UUID, team: UUID) -> None:
    """FK-safe teardown for natural-path fixture."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                DELETE FROM memory_references
                WHERE source_memory_id IN (SELECT id FROM memories WHERE tenant_id = $1)
                   OR target_memory_id IN (SELECT id FROM memories WHERE tenant_id = $1)
                """,
                tenant,
            )
            await conn.execute("DELETE FROM slugs WHERE team_id = $1", team)
            await conn.execute("DELETE FROM memories WHERE tenant_id = $1", tenant)
            await conn.execute("DELETE FROM team_role_assignments WHERE parent_team_id = $1", team)
            await conn.execute("DELETE FROM tenant_identities WHERE tenant_id = $1", tenant)
            await conn.execute("DELETE FROM teams WHERE id = $1", team)
            await conn.execute("DELETE FROM tenants WHERE id = $1", tenant)


class TestItRv1NaturalPath:
    """IT-RV-1: end-to-end PMO DO repro of bug bf7b3379 via the SDK."""

    async def test_same_tenant_references_write_succeeds_post_fix(
        self, pg_pool: Any, sdk_client_factory: Any
    ) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                setup = await _natural_path_setup(conn)
            try:
                tenant = setup["tenant"]

                sdk = sdk_client_factory(tenant_id=tenant, plugin_id="natural-path-test")

                # Step 2: Write target memory M1 via SDK.
                # SDK auto-resolves team_id from tenant_identities.default_team_id
                # (which the natural-path fixture set to `team`).
                target_id = await sdk.write(
                    content="target memory for ref",
                    type="note",
                )
                assert target_id is not None

                # Step 3: Verify M1 readable via memory_get.
                got = await sdk.get(memory_id=target_id)
                assert got is not None
                assert str(got["id"]) == str(target_id)

                # Step 4 & 5: Write citer with references=[target_uuid=M1].
                # PRE-FIX: this raises PluginValidationError(code="memory_not_accessible").
                # POST-FIX: succeeds.
                citer_id = await sdk.write(
                    content="citer memory with reference",
                    type="note",
                    references=[
                        ReferenceInput(target_uuid=UUID(str(target_id)), reference_kind="cites")
                    ],
                )
                assert citer_id is not None

                # Verify memory_references row created.
                async with pg_pool.acquire() as vconn:
                    refs_count = await vconn.fetchval(
                        "SELECT COUNT(*) FROM memory_references "
                        "WHERE source_memory_id = $1 AND target_memory_id = $2",
                        UUID(str(citer_id)),
                        UUID(str(target_id)),
                    )
                    assert refs_count == 1
            finally:
                await _cleanup_natural(pg_pool, setup["tenant"], setup["team"])

    async def test_cross_tenant_still_rejects_opaquely(
        self, pg_pool: Any, sdk_client_factory: Any
    ) -> None:
        """Regression-lock: IT-08 opaque cross-team rejection preserved."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                t1 = await _natural_path_setup(conn)
                t2 = await _natural_path_setup(conn)
            try:
                # Write target M2 in T2's tenant + team.
                sdk_t2 = sdk_client_factory(tenant_id=t2["tenant"], plugin_id="natural-path-test")
                m2 = await sdk_t2.write(
                    content="t2 target",
                    type="note",
                )

                # T1 attempts a reference to T2's memory → MUST opaque-reject.
                sdk_t1 = sdk_client_factory(tenant_id=t1["tenant"], plugin_id="natural-path-test")
                with pytest.raises(PluginValidationError) as exc:
                    await sdk_t1.write(
                        content="t1 cross-tenant citer",
                        type="note",
                        references=[
                            ReferenceInput(target_uuid=UUID(str(m2)), reference_kind="cites")
                        ],
                    )
                assert exc.value.code == "memory_not_accessible"
            finally:
                await _cleanup_natural(pg_pool, t1["tenant"], t1["team"])
                await _cleanup_natural(pg_pool, t2["tenant"], t2["team"])


class TestCarveOutRegressionLocks:
    """B-3: Sites 5 (slug_lookup), 6 (get_batch slug-path), 8 (memory_get slug-path)
    are deliberately CARVED OUT of the same-tenant escape. Lock that they still
    UEA-gate so a future "consistency" PR doesn't accidentally unify them."""

    async def test_site5_slug_lookup_still_uea_gated(
        self, pg_pool: Any, multi_tenant_team_setup: Any, sdk_client_factory: Any
    ) -> None:
        """Site 5: memsys_slug_lookup must still reject when caller has no UEA
        for the queried team — even if same-tenant memories exist there."""
        # tenant_b tries to look up a slug in team_a (no UEA for team_a).
        sdk = sdk_client_factory(tenant_id=multi_tenant_team_setup.tenant_b)
        result = await sdk.slug_lookup(
            team_id=multi_tenant_team_setup.team_a,
            resource_type="decision",
            slug="any-slug-that-may-or-may-not-exist",
        )
        # Opaque carve-out: returns None (per IT-08 contract preserved at this gate).
        assert result is None

    async def test_site6_get_batch_slug_path_still_uea_gated(
        self, pg_pool: Any, multi_tenant_team_setup: Any, sdk_client_factory: Any
    ) -> None:
        """Site 6: get_batch slug-tuple path must still reject when caller
        has no UEA for the queried team."""
        sdk = sdk_client_factory(tenant_id=multi_tenant_team_setup.tenant_b)
        results = await sdk.get_batch(
            requests=[
                {
                    "team_id": str(multi_tenant_team_setup.team_a),
                    "resource_type": "decision",
                    "slug": "any-slug",
                }
            ]
        )
        # Cross-team slug-lookup returns IT-08 opaque error envelope per entry.
        assert len(results) == 1
        assert results[0].get("error") is not None or results[0].get("memory") is None

    async def test_site8_memory_get_slug_path_still_uea_gated(
        self, pg_pool: Any, multi_tenant_team_setup: Any
    ) -> None:
        """Site 8: memory_get slug path must still reject when caller has no
        UEA for the queried team. Exercised at the MCP-tool layer (no SDK
        slug-get wrapper)."""
        from mem_mcp.audit.logger import NoopAuditLogger
        from mem_mcp.mcp.tools._deps import NoopQuotas, ToolDeps
        from mem_mcp.mcp.tools.get import MemoryGetInput, MemoryGetTool

        class _StubEmbed:
            async def embed(self, content: str) -> Any:
                from mem_mcp.embeddings.bedrock import EmbedResult

                return EmbedResult(vector=[0.0] * 1024, input_tokens=1)

        deps = ToolDeps(
            embeddings=_StubEmbed(),
            audit=NoopAuditLogger(),
            quotas=NoopQuotas(),
        )
        ctx = ToolContext(
            request_id=str(uuid4()),
            tenant_id=multi_tenant_team_setup.tenant_b,
            identity_id=multi_tenant_team_setup.tenant_b,
            client_id="plugin:carveout-test",
            scopes=frozenset({"memory.read"}),
            db_pool=pg_pool,
            deps=deps,
        )
        tool = MemoryGetTool()
        # tenant_b queries by slug in team_a → opaque reject.
        from mem_mcp.mcp.errors import JsonRpcError

        with pytest.raises(JsonRpcError) as exc:
            await tool(
                ctx,
                MemoryGetInput(
                    team_id=multi_tenant_team_setup.team_a,
                    resource_type="decision",
                    slug="any-slug",
                ),
            )
        # IT-08 opaque envelope preserved at this site.
        assert exc.value.code in (-32602, -32601)
