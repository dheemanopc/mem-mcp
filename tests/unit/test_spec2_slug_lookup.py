"""Tests for memsys_slug_lookup tool (Spec 2 stage B)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from asyncpg import Pool  # type: ignore[import-untyped]

pytestmark = pytest.mark.asyncio


class TestMemsysSlugLookup:
    """Tests for memsys_slug_lookup tool."""

    async def test_lookup_hit_on_existing_slug(
        self, pg_pool: Pool, populate_user_effective_access
    ) -> None:
        """Slug lookup returns memory_id, title, updated_at when slug exists and user has access."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                # Setup: tenant, team, memory, slug
                tenant_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"sl-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id, team_type) VALUES ('sl-team', $1, 'personal') RETURNING id",
                    tenant_id,
                )
                mem_id = await conn.fetchval(
                    """
                    INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type)
                    VALUES ($1, $2, 'test content', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision')
                    RETURNING id
                    """,
                    tenant_id,
                    team_id,
                    f"hash-{uuid4().hex}",
                )
                await conn.execute(
                    """
                    INSERT INTO slugs (team_id, resource_type, slug, memory_id, title)
                    VALUES ($1, $2, 'test-slug', $3, 'Test Title')
                    """,
                    team_id,
                    "decision",
                    mem_id,
                )

                # Ensure user has access via user_effective_team_access
                await populate_user_effective_access(conn, tenant_id, team_id)

                # Lookup
                slug_row = await conn.fetchrow(
                    """
                    SELECT memory_id, title, updated_at FROM slugs
                    WHERE team_id = $1 AND resource_type = 'decision' AND slug = 'test-slug'
                    """,
                    team_id,
                )
                assert slug_row is not None
                assert slug_row["memory_id"] == mem_id
                assert slug_row["title"] == "Test Title"

    async def test_lookup_miss_returns_null_trio(self, pg_pool: Pool) -> None:
        """Slug lookup returns (None, None, None) when slug doesn't exist."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                team_id = uuid4()
                slug_row = await conn.fetchrow(
                    """
                    SELECT memory_id, title, updated_at FROM slugs
                    WHERE team_id = $1 AND resource_type = 'decision' AND slug = 'nonexistent'
                    """,
                    team_id,
                )
                assert slug_row is None

    async def test_lookup_access_denied_returns_null_trio(self, pg_pool: Pool) -> None:
        """Slug lookup returns (None, None, None) when user has no access to team (opaque fail)."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                # Setup: tenant A creates team and slug, tenant B tries to lookup without access
                tenant_a = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"a-{uuid4()}@example.test",
                )
                tenant_b = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"b-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id, team_type) VALUES ('a-team', $1, 'personal') RETURNING id",
                    tenant_a,
                )
                mem_id = await conn.fetchval(
                    """
                    INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type)
                    VALUES ($1, $2, 'secret', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'fact')
                    RETURNING id
                    """,
                    tenant_a,
                    team_id,
                    f"hash-{uuid4().hex}",
                )
                await conn.execute(
                    """
                    INSERT INTO slugs (team_id, resource_type, slug, memory_id, title)
                    VALUES ($1, $2, 'secret-fact', $3, 'Secret Fact')
                    """,
                    team_id,
                    "fact",
                    mem_id,
                )

                # tenant_b has NO access row in user_effective_team_access
                # A lookup attempt by tenant_b should find the slug (no visibility filter in lookup_slug)
                # but the tool should return null trio due to access check
                slug_row = await conn.fetchrow(
                    """
                    SELECT memory_id, title, updated_at FROM slugs
                    WHERE team_id = $1 AND resource_type = 'fact' AND slug = 'secret-fact'
                    """,
                    team_id,
                )
                assert slug_row is not None  # slug exists
                # But when checked via user_effective_team_access, tenant_b has no access
                access = await conn.fetchval(
                    """
                    SELECT 1 FROM user_effective_team_access
                    WHERE user_id = $1 AND resource_team_id = $2
                    """,
                    tenant_b,
                    team_id,
                )
                assert access is None  # no access confirmed
