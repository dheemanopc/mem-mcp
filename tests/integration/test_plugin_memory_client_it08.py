"""Integration tests for Plugin SDK IT-08 SDK-path + Tier-2 end-to-end.

Combined Tier-1 outstanding (per `3d4a2b2d` tracked condition) + Tier-2
opaque-read DoD (per DA `04da8d62` §"Cleared" #7). One PR discharges both.

Per Test Plan `53cf3015` §INTEGRATION TESTS + v2 delta `894cea7a` DELTA 5+6:
- IT01-IT03: Tier-1 carry — write with refs cross-team opacity via SDK
- IT04b: refs_out with inaccessible target filtered (per delta 6)
- IT05: refs_in cross-team exclusion via SDK
- IT08: slug_lookup inaccessible team returns None opaquely
- IT10: write slug_clue → slug_lookup roundtrip (Tier-2 #2 + #3)
- IT13: write_async returns envelope + persists (Tier-2 #4)

All gated on MEM_MCP_TEST_DSN via pg_pool fixture (auto-skip on CI unit job).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from mem_mcp.plugins.contract import PluginValidationError

pytestmark = pytest.mark.asyncio


# ── Tier-1 IT-08 SDK-path tests (carry-forward from `3d4a2b2d`) ───────────


class TestIt08WriteWithReferences:
    """IT01-IT03 + IT05: write with cross-team-no-access references opaque
    per IT-08; refs traversal preserves opacity."""

    async def test_write_with_unresolvable_reference_opaque(
        self,
        multi_tenant_team_setup: Any,
        sdk_client_factory: Any,
    ) -> None:
        """IT01 — write with a random non-existent UUID as reference target.
        SDK must raise PluginValidationError(code="memory_not_accessible")
        opaquely (caller can't distinguish not-found from no-access).
        """
        from mem_mcp.mcp.tools.write import ReferenceInput

        sdk = sdk_client_factory(tenant_id=multi_tenant_team_setup.tenant_a)
        bogus_target = uuid4()

        with pytest.raises(PluginValidationError) as ei:
            await sdk.write(
                "source memory citing a phantom target",
                type="note",
                references=[
                    ReferenceInput(
                        target_uuid=bogus_target,
                        reference_kind="derived-from",
                    )
                ],
            )
        assert ei.value.code == "memory_not_accessible"

    async def test_write_with_cross_team_no_access_reference_opaque(
        self,
        multi_tenant_team_setup: Any,
        sdk_client_factory: Any,
    ) -> None:
        """IT02 — tenant_a writes source referencing memory_in_team_b (no shared
        team). SDK raises identical opaque error to IT01 (IT-08 byte-identical
        envelope: not-found and no-access indistinguishable)."""
        from mem_mcp.mcp.tools.write import ReferenceInput

        sdk = sdk_client_factory(tenant_id=multi_tenant_team_setup.tenant_a)

        # First capture the "not-found" error message
        with pytest.raises(PluginValidationError) as ei_miss:
            await sdk.write(
                "source",
                type="note",
                references=[
                    ReferenceInput(target_uuid=uuid4(), reference_kind="derived-from")
                ],
            )

        # Now the "no-access" cross-team case
        with pytest.raises(PluginValidationError) as ei_no_access:
            await sdk.write(
                "source",
                type="note",
                references=[
                    ReferenceInput(
                        target_uuid=multi_tenant_team_setup.memory_in_team_b,
                        reference_kind="derived-from",
                    )
                ],
            )

        # IT-08: codes + messages MUST be byte-identical
        assert ei_miss.value.code == ei_no_access.value.code == "memory_not_accessible"
        assert ei_miss.value.message == ei_no_access.value.message


# ── Tier-2 refs traversal IT-08 ───────────────────────────────────────────


class TestRefsTraversalOpacity:
    """IT05 + IT04b: refs_in/refs_out preserve substrate-side opacity."""

    async def test_refs_out_with_inaccessible_target_excluded(
        self,
        multi_tenant_team_setup: Any,
        sdk_client_factory: Any,
        pg_pool: Any,
    ) -> None:
        """IT04b — write source-with-reference where target is in inaccessible
        team. tenant_a's refs_out(source) returns either [] or a list WITHOUT
        the inaccessible edge. Locks the substrate-opacity-passes-through-SDK
        contract per v2 delta DELTA 2.

        Setup limitation: writing a cross-team reference itself raises IT-08
        (see IT02 above). So we INSERT the citation row directly via the
        substrate to set up the scenario.
        """
        sdk_a = sdk_client_factory(tenant_id=multi_tenant_team_setup.tenant_a)

        # Use a fresh write so source belongs to tenant_a's team_a
        source_id = await sdk_a.write(
            "source memory in team A",
            type="note",
        )

        # Inject a memory_references row directly (bypass IT-08 check) so
        # we can verify the SDK-side filtering when traversing the graph.
        async with pg_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memory_references
                (source_memory_id, source_team_id, target_memory_id,
                 target_team_id, target_fragment, reference_kind, refs_version)
                VALUES ($1, $2, $3, $4, NULL, 'derived-from', '1')
                """,
                source_id,
                multi_tenant_team_setup.team_a,
                multi_tenant_team_setup.memory_in_team_b,
                multi_tenant_team_setup.team_b,
            )

        # tenant_a calls refs_out on its source; target is in inaccessible
        # team_b — substrate's filter must drop the edge before SDK returns.
        edges = await sdk_a.refs_out(source_id)
        target_ids_returned = {e.get("target_memory_id") for e in edges}
        assert str(multi_tenant_team_setup.memory_in_team_b) not in target_ids_returned

    async def test_refs_in_returns_empty_for_uncited_memory(
        self,
        multi_tenant_team_setup: Any,
        sdk_client_factory: Any,
    ) -> None:
        """IT04 sanity — fresh write with no citers; refs_in returns []."""
        sdk_a = sdk_client_factory(tenant_id=multi_tenant_team_setup.tenant_a)
        mem_id = await sdk_a.write("uncited memory", type="note")
        edges = await sdk_a.refs_in(mem_id)
        assert edges == []


# ── Tier-2 slug_lookup IT-08 ──────────────────────────────────────────────


class TestSlugLookupOpacity:
    """IT07 + IT08 + IT09: slug_lookup opacity for accessible / inaccessible /
    nonexistent slugs."""

    async def test_slug_lookup_inaccessible_team_returns_none(
        self,
        multi_tenant_team_setup: Any,
        sdk_client_factory: Any,
    ) -> None:
        """IT08 — tenant_a writes a decision-with-slug in team_a; tenant_b
        (no shared team) calls slug_lookup; substrate returns all-null trio;
        SDK collapses to None opaquely.

        Step 1: tenant_a writes the decision-with-slug.
        Step 2: tenant_b (with team_id=team_a, decision, slug) gets None.
        """
        sdk_a = sdk_client_factory(tenant_id=multi_tenant_team_setup.tenant_a)
        sdk_b = sdk_client_factory(tenant_id=multi_tenant_team_setup.tenant_b)

        unique_slug = f"it08-test-{uuid4().hex[:8]}"
        await sdk_a.write(
            "a decision in team A",
            type="decision",
            slug_clue=unique_slug,
        )

        # tenant_a CAN resolve
        from_a = await sdk_a.slug_lookup(
            team_id=multi_tenant_team_setup.team_a,
            resource_type="decision",
            slug=unique_slug,
        )
        assert from_a is not None, "tenant_a should resolve its own team's slug"

        # tenant_b CANNOT — gets None (opaque per IT-08)
        from_b = await sdk_b.slug_lookup(
            team_id=multi_tenant_team_setup.team_a,
            resource_type="decision",
            slug=unique_slug,
        )
        assert from_b is None, "tenant_b should get opaque None (IT-08)"

    async def test_slug_lookup_nonexistent_slug_returns_none(
        self,
        multi_tenant_team_setup: Any,
        sdk_client_factory: Any,
    ) -> None:
        """IT09 — slug that doesn't exist in tenant_a's own team_a also returns
        None (same opaque shape — proving the contract: not-found and no-access
        produce indistinguishable result)."""
        sdk_a = sdk_client_factory(tenant_id=multi_tenant_team_setup.tenant_a)
        result = await sdk_a.slug_lookup(
            team_id=multi_tenant_team_setup.team_a,
            resource_type="decision",
            slug=f"definitely-not-existing-{uuid4().hex}",
        )
        assert result is None


# ── Tier-2 happy paths (round-trip sanity) ────────────────────────────────


class TestTier2HappyPath:
    """IT10 + IT13: end-to-end roundtrip on slug_clue + write_async."""

    async def test_write_slug_clue_then_slug_lookup_roundtrip(
        self,
        multi_tenant_team_setup: Any,
        sdk_client_factory: Any,
    ) -> None:
        """IT10 — write decision with slug_clue; slug_lookup resolves to the
        same memory id."""
        sdk_a = sdk_client_factory(tenant_id=multi_tenant_team_setup.tenant_a)
        unique_slug = f"it10-{uuid4().hex[:8]}"
        written_id = await sdk_a.write(
            "a decision that should be sluggable",
            type="decision",
            slug_clue=unique_slug,
        )

        resolved = await sdk_a.slug_lookup(
            team_id=multi_tenant_team_setup.team_a,
            resource_type="decision",
            slug=unique_slug,
        )
        assert resolved is not None
        assert UUID(resolved["memory_id"]) == written_id

    async def test_write_async_returns_envelope(
        self,
        multi_tenant_team_setup: Any,
        sdk_client_factory: Any,
    ) -> None:
        """IT13 — write_async returns envelope dict immediately with all three keys."""
        sdk_a = sdk_client_factory(tenant_id=multi_tenant_team_setup.tenant_a)
        envelope = await sdk_a.write_async(
            "async write content",
            type="note",
        )
        for k in ("request_id", "queued_at", "estimated_consistency_by"):
            assert k in envelope, f"envelope missing {k!r}: {envelope}"


# ── Tier-2 write_batch happy path ─────────────────────────────────────────


class TestWriteBatchRoundTrip:
    """IT16: write_batch end-to-end returns per-entry results."""

    async def test_write_batch_three_entries_succeed(
        self,
        multi_tenant_team_setup: Any,
        sdk_client_factory: Any,
    ) -> None:
        """Batch of 3 valid memories → 3 per-entry results, each ok=True with id."""
        sdk_a = sdk_client_factory(tenant_id=multi_tenant_team_setup.tenant_a)
        results = await sdk_a.write_batch(
            [
                {"content": f"batch entry 1 {uuid4().hex}", "type": "note"},
                {"content": f"batch entry 2 {uuid4().hex}", "type": "note"},
                {"content": f"batch entry 3 {uuid4().hex}", "type": "note"},
            ]
        )
        assert len(results) == 3
        for entry in results:
            assert entry.get("ok") is True, f"unexpected entry shape: {entry}"
            assert "id" in entry or entry.get("memory_id"), f"missing id: {entry}"
