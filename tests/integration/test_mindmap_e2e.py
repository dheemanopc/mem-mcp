"""End-to-end integration test for the mind-map lifecycle.

Exercises the real MCP tools against a live Postgres pool:
open → write_node (capture + ratify + counter + split flag) → get →
exclusive-ownership guarantee → review (counter reset) → close (graduation:
promote-as-new-write with promoted-from backlink, then archive) →
no-write-after-archive. Also covers the from_spec dedup guard.

Mirrors the setup/cleanup style of tests/integration/conftest.py.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest

from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools.mindmap_close import (
    ConfirmedDecision,
    MindmapCloseInput,
    MindmapCloseTool,
)
from mem_mcp.mcp.tools.mindmap_get import MindmapGetInput, MindmapGetTool
from mem_mcp.mcp.tools.mindmap_open import MindmapOpenInput, MindmapOpenTool
from mem_mcp.mcp.tools.mindmap_review import MindmapReviewInput, MindmapReviewTool
from mem_mcp.mcp.tools.mindmap_write_node import (
    MindmapWriteNodeInput,
    MindmapWriteNodeTool,
)
from mem_mcp.mindmap import service
from mem_mcp.teams.assignments import assign_role

pytestmark = pytest.mark.asyncio


def _build_deps() -> Any:
    from mem_mcp.audit.logger import NoopAuditLogger
    from mem_mcp.mcp.tools._deps import NoopQuotas, ToolDeps

    class _StubEmbed:
        async def embed(self, content: str) -> Any:
            from mem_mcp.embeddings.bedrock import EmbedResult

            return EmbedResult(vector=[0.0] * 1024, input_tokens=max(1, len(content) // 4))

    return ToolDeps(embeddings=_StubEmbed(), audit=NoopAuditLogger(), quotas=NoopQuotas())


async def _setup(conn: asyncpg.Connection) -> dict[str, Any]:
    tenant = await conn.fetchval(
        "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
        f"mindmap-{uuid4()}@example.test",
    )
    team = await conn.fetchval(
        "INSERT INTO teams (name, created_by_tenant_id) VALUES ($1, $2) RETURNING id",
        f"mindmap-team-{uuid4().hex[:8]}",
        tenant,
    )
    await assign_role(
        conn,
        parent_team_id=team,
        member_kind="user",
        member_id=tenant,
        role_key="member",
        assigned_by_user_id=tenant,
    )
    await conn.execute(
        """
        INSERT INTO tenant_identities
            (tenant_id, cognito_sub, provider, email, is_primary, default_team_id)
        VALUES ($1, $2, 'cognito', $3, true, $4)
        """,
        tenant,
        f"sub-{tenant}",
        f"mindmap-{tenant}@example.test",
        team,
    )
    client_id = f"client:mindmap-{uuid4().hex[:8]}"
    await conn.execute(
        """
        INSERT INTO oauth_clients (id, redirect_uris, scope, registration_payload, review_status)
        VALUES ($1, ARRAY[]::text[], 'memory.read memory.write', '{}'::jsonb, 'auto_allowed')
        """,
        client_id,
    )
    return {"tenant": tenant, "team": team, "client_id": client_id}


async def _cleanup(pool: Any, tenant: UUID, team: UUID, client_id: str) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM memory_references WHERE source_memory_id IN "
                "(SELECT id FROM memories WHERE tenant_id = $1) "
                "OR target_memory_id IN (SELECT id FROM memories WHERE tenant_id = $1)",
                tenant,
            )
            await conn.execute("DELETE FROM memory_map_events WHERE tenant_id = $1", tenant)
            await conn.execute("DELETE FROM memory_map_membership WHERE tenant_id = $1", tenant)
            await conn.execute("DELETE FROM memory_maps WHERE tenant_id = $1", tenant)
            await conn.execute("DELETE FROM slugs WHERE team_id = $1", team)
            await conn.execute("DELETE FROM memories WHERE tenant_id = $1", tenant)
            await conn.execute("DELETE FROM team_role_assignments WHERE parent_team_id = $1", team)
            await conn.execute("DELETE FROM tenant_identities WHERE tenant_id = $1", tenant)
            await conn.execute("DELETE FROM teams WHERE id = $1", team)
            await conn.execute("DELETE FROM tenants WHERE id = $1", tenant)
            await conn.execute("DELETE FROM oauth_clients WHERE id = $1", client_id)


def _ctx(pool: Any, tenant: UUID, client_id: str) -> ToolContext:
    return ToolContext(
        request_id=f"req-{uuid4().hex[:8]}",
        tenant_id=tenant,
        identity_id=tenant,
        client_id=client_id,
        scopes=frozenset({"memory.read", "memory.write"}),
        db_pool=pool,
        deps=_build_deps(),
    )


async def test_mindmap_full_lifecycle(pg_pool: Any) -> None:
    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            s = await _setup(conn)
    tenant, team, client_id = s["tenant"], s["team"], s["client_id"]
    ctx = _ctx(pg_pool, tenant, client_id)

    try:
        # ---- open ----------------------------------------------------------
        opened = await MindmapOpenTool()(
            ctx,
            MindmapOpenInput(
                title="Caching strategy",
                root_question="What caching approach fits our read/write mix?",
                review_threshold=3,  # low so review_due trips quickly
            ),
        )
        map_key = opened.map_key
        assert map_key  # a slug was minted as the map key
        assert opened.state == "live"
        assert not opened.deduped

        # ---- write nodes (capture + ratify) --------------------------------
        n1 = await MindmapWriteNodeTool()(
            ctx,
            MindmapWriteNodeInput(
                map_key=map_key,
                content="Use Redis for the hot read path",
                node_role="position",
                ratification_strength="survived-challenge",
                ratification_citation="user: yes, Redis, we benchmarked it",
                responsible_party="owner",
            ),
        )
        assert n1.writes_since_review == 1
        assert not n1.review_due

        await MindmapWriteNodeTool()(
            ctx,
            MindmapWriteNodeInput(
                map_key=map_key,
                content="But Redis adds an ops burden and a new failure mode",
                node_role="challenge",
            ),
        )
        # third write hits threshold=3 → review_due
        n3 = await MindmapWriteNodeTool()(
            ctx,
            MindmapWriteNodeInput(
                map_key=map_key, content="Cache invalidation policy is TTL-based", node_role="note"
            ),
        )
        assert n3.writes_since_review == 3
        assert n3.review_due is True

        # split-suggested flag on an oversized node (advisory; still written)
        big = await MindmapWriteNodeTool()(
            ctx,
            MindmapWriteNodeInput(map_key=map_key, content="x" * 1300, node_role="position"),
        )
        assert big.split_suggested is True

        # ---- get -----------------------------------------------------------
        got = await MindmapGetTool()(ctx, MindmapGetInput(map_key=map_key))
        assert got.state == "live"
        # root + 4 nodes
        assert len(got.nodes) == 5
        roles = sorted(n.node_role for n in got.nodes)
        assert roles == ["challenge", "note", "position", "position", "root"]
        # whose-turn: n1 was responsible_party=owner → open loop
        assert n1.node_id in got.open_loops

        # ---- exclusive ownership is structural -----------------------------
        async with pg_pool.acquire() as conn:
            with pytest.raises(asyncpg.exceptions.UniqueViolationError):
                async with conn.transaction():
                    await conn.execute(
                        "SELECT set_config('app.current_tenant_id', $1, true)", str(tenant)
                    )
                    await service.insert_membership(
                        conn,
                        memory_id=n1.node_id,  # already owned by this map
                        root_memory_id=opened.root_memory_id,
                        tenant_id=tenant,
                        node_role="position",
                    )

        # ---- review resets the counter -------------------------------------
        # four write_node calls bumped the counter (n1, n2, n3, big) → reset from 4
        reviewed = await MindmapReviewTool()(ctx, MindmapReviewInput(map_key=map_key))
        assert reviewed.counter_reset_from == 4
        got2 = await MindmapGetTool()(ctx, MindmapGetInput(map_key=map_key))
        assert got2.writes_since_review == 0
        assert got2.review_due is False

        # ---- close / graduation --------------------------------------------
        closed = await MindmapCloseTool()(
            ctx,
            MindmapCloseInput(
                map_key=map_key,
                confirmed_decisions=[
                    ConfirmedDecision(
                        content="Adopt Redis for the hot read path",
                        slug_clue="redis-hot-path",
                        from_node_id=n1.node_id,
                        ratification_strength="delegate",  # → verify_substance
                    )
                ],
                summary="Caching strategy resolved: Redis hot path with TTL invalidation.",
            ),
        )
        assert closed.state == "archived"
        assert len(closed.promoted) == 1
        promo = closed.promoted[0]
        assert promo.slug == "redis-hot-path"
        assert promo.verify_substance is True  # delegate strength
        assert closed.graduated_index_id is not None

        # promotion is a NEW decision with a promoted-from backlink, not a supersede
        async with pg_pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(tenant))
            dtype = await conn.fetchval(
                "SELECT type FROM memories WHERE id = $1", promo.decision_id
            )
            assert dtype == "decision"
            backlink = await conn.fetchval(
                "SELECT reference_kind FROM memory_references "
                "WHERE source_memory_id = $1 AND target_memory_id = $2",
                promo.decision_id,
                n1.node_id,
            )
            assert backlink == "promoted-from"

        # ---- no writes to an archived map (no-reopen) ----------------------
        with pytest.raises(JsonRpcError):
            await MindmapWriteNodeTool()(
                ctx,
                MindmapWriteNodeInput(
                    map_key=map_key, content="late addition", node_role="position"
                ),
            )
    finally:
        await _cleanup(pg_pool, tenant, team, client_id)


async def test_mindmap_from_spec_dedup(pg_pool: Any) -> None:
    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            s = await _setup(conn)
            # a KB memory to seed from
            seed = await conn.fetchval(
                "INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, "
                "source_kind, type, visibility) VALUES ($1, $2, 'seed decision', $3, "
                "array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision', 'team') RETURNING id",
                s["tenant"],
                s["team"],
                f"hash-{uuid4().hex}",
            )
    tenant, team, client_id = s["tenant"], s["team"], s["client_id"]
    ctx = _ctx(pg_pool, tenant, client_id)
    try:
        first = await MindmapOpenTool()(
            ctx,
            MindmapOpenInput(title="Revisit caching", root_question="Reopen?", seed_memory_id=seed),
        )
        assert first.deduped is False
        # second open from the SAME live seed → deduped to the existing map
        second = await MindmapOpenTool()(
            ctx,
            MindmapOpenInput(
                title="Revisit caching again", root_question="Reopen?", seed_memory_id=seed
            ),
        )
        assert second.deduped is True
        assert second.root_memory_id == first.root_memory_id
        assert second.map_key == first.map_key
    finally:
        await _cleanup(pg_pool, tenant, team, client_id)
