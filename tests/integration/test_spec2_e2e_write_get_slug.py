"""Integration test IT-06 — Spec 2 end-to-end write→get via slug lookup.

Exercise the full MemoryWriteTool and MemoryGetTool path with slug-lookup.
This integration test catches the get.py bug (inp.id vs lookup_id) that
pure-Pydantic stage-B tests missed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from mem_mcp.audit.logger import NoopAuditLogger
from mem_mcp.embeddings.bedrock import EmbedResult
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._deps import NoopQuotas, ToolDeps
from mem_mcp.mcp.tools.get import MemoryGetInput, MemoryGetOutput, MemoryGetTool
from mem_mcp.mcp.tools.write import MemoryWriteInput, MemoryWriteOutput, MemoryWriteTool
from mem_mcp.teams.slugs import lookup_slug

if TYPE_CHECKING:
    from asyncpg import Pool  # type: ignore[import-untyped]

pytestmark = pytest.mark.asyncio


class FakeEmbeddings:
    """Stub embeddings for test context."""

    async def embed(self, text: str) -> EmbedResult:
        return EmbedResult(vector=[0.1] * 1024, input_tokens=12)


class TestIT06_Spec2WriteGetSlugLookup:  # noqa: N801
    """End-to-end: write decision with slug, get it by slug, verify bug fix."""

    async def test_write_and_get_via_slug_lookup(self, pg_pool: Pool) -> None:
        """Write a decision with slug_clue, get it by (team_id, resource_type, slug)."""
        # Setup
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                identity_id = await conn.fetchval(
                    """
                    INSERT INTO tenant_identities
                    (tenant_id, cognito_sub, provider, email, is_primary)
                    VALUES ($1, $2, 'cognito', $3, true)
                    RETURNING id
                    """,
                    tenant_id,
                    f"sub-{tenant_id}",
                    f"user-{tenant_id}@test.invalid",
                )
                # Create personal team for the tenant
                personal_team_id = await conn.fetchval(
                    """
                    INSERT INTO teams (name, created_by_tenant_id, team_type)
                    VALUES ($1, $2, 'personal')
                    RETURNING id
                    """,
                    f"Personal — {tenant_id}",
                    tenant_id,
                )
                # Grant owner role to the tenant
                role_id = await conn.fetchval(
                    "SELECT id FROM roles_catalog WHERE role_key='owner' AND plugin_id IS NULL"
                )
                await conn.execute(
                    """
                    INSERT INTO team_role_assignments
                    (parent_team_id, member_kind, member_id, role_id, status, assigned_by_user_id)
                    VALUES ($1, 'user', $2, $3, 'active', $2)
                    """,
                    personal_team_id,
                    tenant_id,
                    role_id,
                )
                # Set default_team_id on the identity
                await conn.execute(
                    "UPDATE tenant_identities SET default_team_id = $1 WHERE id = $2",
                    personal_team_id,
                    identity_id,
                )
                # Setup user_effective_team_access so the tenant can read from their own team
                await conn.execute(
                    """
                    INSERT INTO user_effective_team_access
                    (user_id, resource_team_id, effective_role_class, effective_perms, path_count)
                    VALUES ($1, $2, 'internal', ARRAY['read', 'write', 'delete'], 1)
                    """,
                    tenant_id,
                    personal_team_id,
                )

        # Build minimal ToolContext with real deps
        deps = ToolDeps(
            embeddings=FakeEmbeddings(),
            audit=NoopAuditLogger(),
            quotas=NoopQuotas(),
        )
        ctx = ToolContext(
            request_id=str(uuid4()),
            tenant_id=tenant_id,
            identity_id=identity_id,
            client_id="test-client",
            scopes=frozenset(["memory.read", "memory.write"]),
            db_pool=pg_pool,
            deps=deps,
        )

        # Task 1: Write a decision with slug_clue
        write_tool = MemoryWriteTool()
        write_input = MemoryWriteInput(
            content="My test decision content",
            type="decision",
            slug_clue="my-test-decision",
            references=[],
            team_id=personal_team_id,
            visibility="team",
        )
        write_output: MemoryWriteOutput = await write_tool(ctx, write_input)  # type: ignore[assignment]
        memory_id = write_output.id

        # Verify slug row exists
        async with pg_pool.acquire() as conn:
            slug_row = await conn.fetchrow(
                """
                SELECT memory_id, slug FROM slugs
                WHERE team_id = $1 AND resource_type = 'decision'
                """,
                personal_team_id,
            )
            assert slug_row is not None, "Slug row not created after write"
            assert slug_row["memory_id"] == memory_id
            assert slug_row["slug"] == "my-test-decision"

        # Task 2: Get the memory by (team_id, resource_type, slug)
        get_tool = MemoryGetTool()
        get_input = MemoryGetInput(
            team_id=personal_team_id,
            resource_type="decision",
            slug="my-test-decision",
        )
        get_output: MemoryGetOutput = await get_tool(ctx, get_input)  # type: ignore[assignment]
        assert get_output.memory.id == memory_id
        assert get_output.memory.content == "My test decision content"
        assert get_output.memory.type == "decision"

        # Task 3: Get with include_history=True (tests the inp.id vs lookup_id bug at line 202)
        get_input_hist = MemoryGetInput(
            team_id=personal_team_id,
            resource_type="decision",
            slug="my-test-decision",
            include_history=True,
        )
        get_output_hist: MemoryGetOutput = await get_tool(ctx, get_input_hist)  # type: ignore[assignment]
        assert get_output_hist.memory.id == memory_id
        assert get_output_hist.history == []  # No prior versions

        # Task 4: Negative test — caller without access to the team
        other_tenant_id = uuid4()
        async with pg_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                other_tenant_id,
                f"other-{other_tenant_id}@test.invalid",
            )
            other_identity_id = await conn.fetchval(
                """
                INSERT INTO tenant_identities
                (tenant_id, cognito_sub, provider, email, is_primary)
                VALUES ($1, $2, 'cognito', $3, true)
                RETURNING id
                """,
                other_tenant_id,
                f"sub-{other_tenant_id}",
                f"other-{other_tenant_id}@test.invalid",
            )

        other_ctx = ToolContext(
            request_id=str(uuid4()),
            tenant_id=other_tenant_id,
            identity_id=other_identity_id,
            client_id="test-client",
            scopes=frozenset(["memory.read", "memory.write"]),
            db_pool=pg_pool,
            deps=deps,
        )
        get_input_no_access = MemoryGetInput(
            team_id=personal_team_id,
            resource_type="decision",
            slug="my-test-decision",
        )
        from mem_mcp.mcp.errors import JsonRpcError

        with pytest.raises(JsonRpcError) as exc_info:
            await get_tool(other_ctx, get_input_no_access)
        assert exc_info.value.code == -32602
        assert "not found or not accessible" in exc_info.value.message

    async def test_slug_lookup_helper_directly(self, pg_pool: Pool) -> None:
        """Unit-level: verify lookup_slug helper works correctly."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ($1, $2) RETURNING id",
                    f"it06-slug-test-{uuid4().hex[:8]}",
                    tenant_id,
                )
                mem_id = await conn.fetchval(
                    """
                    INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type, visibility)
                    VALUES ($1, $2, 'test content', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision', 'team')
                    RETURNING id
                    """,
                    tenant_id,
                    team_id,
                    f"hash-{uuid4().hex}",
                )
                # Insert slug
                from mem_mcp.teams.slugs import insert_slug_with_retry

                slug = await insert_slug_with_retry(
                    conn,
                    team_id=team_id,
                    resource_type="decision",
                    clue="direct-lookup-test",
                    memory_id=mem_id,
                    title="Test",
                )
                assert slug == "direct-lookup-test"

                # Lookup via helper
                slug_row = await lookup_slug(
                    conn,
                    team_id=team_id,
                    resource_type="decision",
                    slug="direct-lookup-test",
                )
                assert slug_row is not None
                assert slug_row["memory_id"] == mem_id
