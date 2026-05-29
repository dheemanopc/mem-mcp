"""Integration test IT-05 — slug uniqueness under concurrent writes.

Per test plan e552d271 Section 2 IT-05 + amendment #14 (PK+retry serialization
via the unique constraint). 10 concurrent writers all sending the same
slug_clue must produce 10 distinct slugs, no race, no duplicate-PK violation
surfaced to callers.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from mem_mcp.teams.slugs import insert_slug_with_retry

if TYPE_CHECKING:
    from asyncpg import Pool  # type: ignore[import-untyped]

pytestmark = pytest.mark.asyncio


class TestIT05_ConcurrentSlugUniqueness:  # noqa: N801
    async def test_10_writers_get_distinct_slugs(self, pg_pool: Pool) -> None:
        """10 asyncio.gather writers all sending clue='dod-design' → 10 distinct slugs."""
        # Setup outside the concurrent block — committed so all workers see it.
        async with pg_pool.acquire() as setup_conn:
            tenant_id = await setup_conn.fetchval(
                "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                f"test-{uuid4()}@example.test",
            )
            team_id = await setup_conn.fetchval(
                "INSERT INTO teams (name, created_by_tenant_id) VALUES ($1, $2) RETURNING id",
                f"it05-{uuid4().hex[:8]}",
                tenant_id,
            )
            # 10 distinct memory rows
            mem_ids = []
            for i in range(10):
                mid = await setup_conn.fetchval(
                    """
                    INSERT INTO memories (tenant_id, content, content_hash, embedding, source_kind, type)
                    VALUES ($1, $2, $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision')
                    RETURNING id
                    """,
                    tenant_id,
                    f"content-{i}",
                    f"hash-{uuid4().hex}",
                )
                mem_ids.append(mid)

        async def worker(memory_id: UUID) -> str:
            async with pg_pool.acquire() as conn:
                async with conn.transaction():
                    return await insert_slug_with_retry(
                        conn,
                        team_id=team_id,
                        resource_type="decision",
                        clue="dod-design",
                        memory_id=memory_id,
                        title="Concurrent test",
                    )

        try:
            results = await asyncio.gather(*[worker(m) for m in mem_ids])
        finally:
            # Cleanup
            async with pg_pool.acquire() as cleanup_conn:
                await cleanup_conn.execute("DELETE FROM slugs WHERE team_id = $1", team_id)
                await cleanup_conn.execute("DELETE FROM memories WHERE tenant_id = $1", tenant_id)
                await cleanup_conn.execute("DELETE FROM teams WHERE id = $1", team_id)
                await cleanup_conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)

        # Every slug must be distinct
        assert len(set(results)) == 10
        # All must match dod-design[-N] pattern
        for s in results:
            assert s == "dod-design" or s.startswith("dod-design-")
        # The base 'dod-design' must be one of them
        assert "dod-design" in results
        # The numbered ones should be from -2 onward (no 'dod-design-1')
        for s in results:
            if s != "dod-design":
                suffix = s.split("-")[-1]
                assert suffix.isdigit()
                assert int(suffix) >= 2


class TestIT05_HappyPath:  # noqa: N801
    async def test_lookup_after_insert(self, pg_pool: Pool) -> None:
        """End-to-end: insert + look up by slug + supersede + slug-redirects-to-new."""
        from mem_mcp.teams.slugs import lookup_slug, redirect_slug_on_supersession

        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ($1, $2) RETURNING id",
                    f"happy-{uuid4().hex[:8]}",
                    tenant_id,
                )
                v1 = await conn.fetchval(
                    "INSERT INTO memories (tenant_id, content, content_hash, embedding, source_kind, type) "
                    "VALUES ($1, 'v1', $2, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision') RETURNING id",
                    tenant_id,
                    f"hash-{uuid4().hex}",
                )
                v2 = await conn.fetchval(
                    "INSERT INTO memories (tenant_id, content, content_hash, embedding, source_kind, type) "
                    "VALUES ($1, 'v2', $2, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision') RETURNING id",
                    tenant_id,
                    f"hash-{uuid4().hex}",
                )

                slug = await insert_slug_with_retry(
                    conn,
                    team_id=team_id,
                    resource_type="decision",
                    clue="naming-conventions",
                    memory_id=v1,
                    title="V1",
                )
                assert slug == "naming-conventions"

                # Lookup returns v1
                got = await lookup_slug(conn, team_id=team_id, resource_type="decision", slug=slug)
                assert got is not None
                assert got["memory_id"] == v1

                # Supersede v1 → v2; slug must redirect
                moved = await redirect_slug_on_supersession(
                    conn, old_memory_id=v1, new_memory_id=v2
                )
                assert moved is True

                # Lookup now returns v2
                got2 = await lookup_slug(conn, team_id=team_id, resource_type="decision", slug=slug)
                assert got2 is not None
                assert got2["memory_id"] == v2
