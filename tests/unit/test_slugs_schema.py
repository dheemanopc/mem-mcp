"""Tests for slugs table schema + constraints."""

from __future__ import annotations

from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest

pytestmark = pytest.mark.asyncio


async def _make_tenant_team_memory(conn: asyncpg.Connection) -> tuple[str, str, str]:
    """Helper: create a tenant + team + memory for slug tests."""
    tenant_id = await conn.fetchval(
        "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
        f"test-{uuid4()}@example.test",
    )
    team_id = await conn.fetchval(
        "INSERT INTO teams (name, created_by_tenant_id) VALUES ($1, $2) RETURNING id",
        f"slug-test-{uuid4().hex[:8]}",
        tenant_id,
    )
    # A memory row needs an embedding; use the zero vector for testing.
    memory_id = await conn.fetchval(
        """
        INSERT INTO memories (tenant_id, content, content_hash, embedding, source_kind, type)
        VALUES ($1, 'test content', $2, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision')
        RETURNING id
        """,
        tenant_id,
        f"hash-{uuid4().hex}",
    )
    return tenant_id, team_id, memory_id


class TestSlugsSchema:
    async def test_table_exists(self, pg_pool: asyncpg.pool.Pool) -> None:
        async with pg_pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'slugs')"
            )
            assert exists is True

    async def test_insert_valid_slug(self, pg_pool: asyncpg.pool.Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tid, team_id, mem_id = await _make_tenant_team_memory(conn)
                await conn.execute(
                    "INSERT INTO slugs (team_id, resource_type, slug, memory_id, title) VALUES ($1, 'decision', 'dod-design', $2, 'DoD Design')",
                    team_id,
                    mem_id,
                )
                cnt = await conn.fetchval(
                    "SELECT count(*) FROM slugs WHERE team_id = $1",
                    team_id,
                )
                assert cnt == 1

    async def test_pk_uniqueness_within_scope(self, pg_pool: asyncpg.pool.Pool) -> None:
        """Same slug allowed across different (team_id, resource_type); duplicate within scope rejects."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tid, team_id, mem_id_1 = await _make_tenant_team_memory(conn)
                mem_id_2 = await conn.fetchval(
                    "INSERT INTO memories (tenant_id, content, content_hash, embedding, source_kind, type) "
                    "VALUES ($1, 'c2', $2, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision') RETURNING id",
                    tid,
                    f"hash-{uuid4().hex}",
                )
                await conn.execute(
                    "INSERT INTO slugs (team_id, resource_type, slug, memory_id, title) VALUES ($1, 'decision', 'shared', $2, 'D1')",
                    team_id,
                    mem_id_1,
                )
                # Same slug + same team + same resource_type → must reject
                async with conn.transaction():
                    with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                        await conn.execute(
                            "INSERT INTO slugs (team_id, resource_type, slug, memory_id, title) VALUES ($1, 'decision', 'shared', $2, 'D2')",
                            team_id,
                            mem_id_2,
                        )

    async def test_same_slug_different_resource_type_allowed(
        self, pg_pool: asyncpg.pool.Pool
    ) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tid, team_id, mem_id = await _make_tenant_team_memory(conn)
                mem_id_2 = await conn.fetchval(
                    "INSERT INTO memories (tenant_id, content, content_hash, embedding, source_kind, type) "
                    "VALUES ($1, 'c2', $2, array_fill(0::real, ARRAY[1024])::vector, 'api', 'fact') RETURNING id",
                    tid,
                    f"hash-{uuid4().hex}",
                )
                await conn.execute(
                    "INSERT INTO slugs (team_id, resource_type, slug, memory_id, title) VALUES ($1, 'decision', 'naming', $2, 'D')",
                    team_id,
                    mem_id,
                )
                await conn.execute(
                    "INSERT INTO slugs (team_id, resource_type, slug, memory_id, title) VALUES ($1, 'fact', 'naming', $2, 'F')",
                    team_id,
                    mem_id_2,
                )
                cnt = await conn.fetchval("SELECT count(*) FROM slugs WHERE team_id = $1", team_id)
                assert cnt == 2

    async def test_resource_type_check_constraint(self, pg_pool: asyncpg.pool.Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tid, team_id, mem_id = await _make_tenant_team_memory(conn)
                with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                    await conn.execute(
                        "INSERT INTO slugs (team_id, resource_type, slug, memory_id, title) VALUES ($1, 'note', 'x', $2, 't')",
                        team_id,
                        mem_id,
                    )

    async def test_slug_regex_rejects_uppercase(self, pg_pool: asyncpg.pool.Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tid, team_id, mem_id = await _make_tenant_team_memory(conn)
                with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                    await conn.execute(
                        "INSERT INTO slugs (team_id, resource_type, slug, memory_id, title) VALUES ($1, 'decision', 'BadSlug', $2, 't')",
                        team_id,
                        mem_id,
                    )

    async def test_slug_regex_rejects_leading_hyphen(self, pg_pool: asyncpg.pool.Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tid, team_id, mem_id = await _make_tenant_team_memory(conn)
                with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                    await conn.execute(
                        "INSERT INTO slugs (team_id, resource_type, slug, memory_id, title) VALUES ($1, 'decision', '-bad', $2, 't')",
                        team_id,
                        mem_id,
                    )

    async def test_slug_length_cap_64(self, pg_pool: asyncpg.pool.Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tid, team_id, mem_id = await _make_tenant_team_memory(conn)
                long_slug = "a" + ("b" * 64)  # 65 chars
                with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                    await conn.execute(
                        "INSERT INTO slugs (team_id, resource_type, slug, memory_id, title) VALUES ($1, 'decision', $2, $3, 't')",
                        team_id,
                        long_slug,
                        mem_id,
                    )

    async def test_hard_delete_memory_blocked_by_slug_fk(self, pg_pool: asyncpg.pool.Pool) -> None:
        """Per Spec 3: slugs.memory_id FK ON DELETE RESTRICT prevents hard-delete of slugged memories."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tid, team_id, mem_id = await _make_tenant_team_memory(conn)
                await conn.execute(
                    "INSERT INTO slugs (team_id, resource_type, slug, memory_id, title) VALUES ($1, 'decision', 'protected', $2, 'P')",
                    team_id,
                    mem_id,
                )
                with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                    await conn.execute("DELETE FROM memories WHERE id = $1", mem_id)
