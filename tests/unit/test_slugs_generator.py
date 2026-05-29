"""Tests for teams.slugs — generator + reserved + supersession + lookup."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from mem_mcp.teams.slugs import (
    ALL_RESERVED_SLUGS,
    SlugClueInvalidError,
    SlugClueRequiredError,
    SlugClueReservedError,
    insert_slug_with_retry,
    lookup_slug,
    normalize_slug_clue,
    redirect_slug_on_supersession,
)

if TYPE_CHECKING:
    from asyncpg import Connection, Pool


pytestmark = pytest.mark.asyncio


async def _make_tenant_team_memory(
    conn: Connection, mtype: str = "decision"
) -> tuple[UUID, UUID, UUID]:
    tenant_id = await conn.fetchval(
        "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
        f"test-{uuid4()}@example.test",
    )
    team_id = await conn.fetchval(
        "INSERT INTO teams (name, created_by_tenant_id) VALUES ($1, $2) RETURNING id",
        f"slug-{uuid4().hex[:8]}",
        tenant_id,
    )
    memory_id = await conn.fetchval(
        """
        INSERT INTO memories (tenant_id, content, content_hash, embedding, source_kind, type)
        VALUES ($1, 'c', $2, array_fill(0::real, ARRAY[1024])::vector, 'api', $3)
        RETURNING id
        """,
        tenant_id,
        f"hash-{uuid4().hex}",
        mtype,
    )
    return tenant_id, team_id, memory_id


class TestNormalizeSlugClue:
    def test_simple_lowercase(self: TestNormalizeSlugClue) -> None:
        assert normalize_slug_clue("dod-design") == "dod-design"

    def test_mixed_case_lowercases(self: TestNormalizeSlugClue) -> None:
        assert normalize_slug_clue("DoD-Design") == "dod-design"

    def test_special_chars_become_hyphens(self: TestNormalizeSlugClue) -> None:
        assert normalize_slug_clue("DoD: design v2") == "dod-design-v2"

    def test_collapses_repeated_hyphens(self: TestNormalizeSlugClue) -> None:
        assert normalize_slug_clue("a---b") == "a-b"

    def test_trims_edges(self: TestNormalizeSlugClue) -> None:
        assert normalize_slug_clue("---abc---") == "abc"

    def test_caps_at_64(self: TestNormalizeSlugClue) -> None:
        long = "a" * 100
        result = normalize_slug_clue(long)
        assert result is not None
        assert len(result) <= 64

    def test_empty_returns_none(self: TestNormalizeSlugClue) -> None:
        assert normalize_slug_clue("") is None
        assert normalize_slug_clue("???!!!") is None

    def test_non_ascii_returns_none(self: TestNormalizeSlugClue) -> None:
        assert normalize_slug_clue("café") is None
        assert normalize_slug_clue("日本") is None


class TestReservedSlugs:
    def test_core_reserved_in_set(self: TestReservedSlugs) -> None:
        for k in (
            "admin",
            "api",
            "health",
            "metrics",
            "internal",
            "system",
            "null",
            "undefined",
            "root",
        ):
            assert k in ALL_RESERVED_SLUGS

    def test_dynamic_handlers_in_set(self: TestReservedSlugs) -> None:
        # The dynamic gather should have picked up at least 'memories' if web/handlers/memories.py exists.
        # Soft assertion: if file exists, name is in set.
        from pathlib import Path

        handlers = (
            Path(__file__).resolve().parent.parent.parent / "src" / "mem_mcp" / "web" / "handlers"
        )
        if (handlers / "memories.py").exists():
            assert "memories" in ALL_RESERVED_SLUGS


class TestInsertSlugWithRetry:
    async def test_empty_clue_raises_required(self: TestInsertSlugWithRetry, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                _, team_id, mem_id = await _make_tenant_team_memory(conn)
                with pytest.raises(SlugClueRequiredError):
                    await insert_slug_with_retry(
                        conn,
                        team_id=team_id,
                        resource_type="decision",
                        clue="",
                        memory_id=mem_id,
                        title="t",
                    )

    async def test_non_ascii_raises_invalid(self: TestInsertSlugWithRetry, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                _, team_id, mem_id = await _make_tenant_team_memory(conn)
                with pytest.raises(SlugClueInvalidError):
                    await insert_slug_with_retry(
                        conn,
                        team_id=team_id,
                        resource_type="decision",
                        clue="日本語",
                        memory_id=mem_id,
                        title="t",
                    )

    async def test_special_only_raises_invalid(
        self: TestInsertSlugWithRetry, pg_pool: Pool
    ) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                _, team_id, mem_id = await _make_tenant_team_memory(conn)
                with pytest.raises(SlugClueInvalidError):
                    await insert_slug_with_retry(
                        conn,
                        team_id=team_id,
                        resource_type="decision",
                        clue="!!!",
                        memory_id=mem_id,
                        title="t",
                    )

    async def test_reserved_raises(self: TestInsertSlugWithRetry, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                _, team_id, mem_id = await _make_tenant_team_memory(conn)
                with pytest.raises(SlugClueReservedError):
                    await insert_slug_with_retry(
                        conn,
                        team_id=team_id,
                        resource_type="decision",
                        clue="admin",
                        memory_id=mem_id,
                        title="t",
                    )

    async def test_first_attempt_succeeds(self: TestInsertSlugWithRetry, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                _, team_id, mem_id = await _make_tenant_team_memory(conn)
                slug = await insert_slug_with_retry(
                    conn,
                    team_id=team_id,
                    resource_type="decision",
                    clue="dod-design",
                    memory_id=mem_id,
                    title="DoD Design",
                )
                assert slug == "dod-design"

    async def test_collision_appends_suffix(self: TestInsertSlugWithRetry, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tid, team_id, mem_id_1 = await _make_tenant_team_memory(conn)
                mem_id_2 = await conn.fetchval(
                    "INSERT INTO memories (tenant_id, content, content_hash, embedding, source_kind, type) "
                    "VALUES ($1, 'c2', $2, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision') RETURNING id",
                    tid,
                    f"hash-{uuid4().hex}",
                )
                s1 = await insert_slug_with_retry(
                    conn,
                    team_id=team_id,
                    resource_type="decision",
                    clue="naming",
                    memory_id=mem_id_1,
                    title="T1",
                )
                s2 = await insert_slug_with_retry(
                    conn,
                    team_id=team_id,
                    resource_type="decision",
                    clue="naming",
                    memory_id=mem_id_2,
                    title="T2",
                )
                assert s1 == "naming"
                assert s2 == "naming-2"


class TestRedirectOnSupersession:
    async def test_redirects(self: TestRedirectOnSupersession, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tid, team_id, old_mem = await _make_tenant_team_memory(conn)
                new_mem = await conn.fetchval(
                    "INSERT INTO memories (tenant_id, content, content_hash, embedding, source_kind, type) "
                    "VALUES ($1, 'new', $2, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision') RETURNING id",
                    tid,
                    f"hash-{uuid4().hex}",
                )
                await insert_slug_with_retry(
                    conn,
                    team_id=team_id,
                    resource_type="decision",
                    clue="evolving",
                    memory_id=old_mem,
                    title="V1",
                )
                moved = await redirect_slug_on_supersession(
                    conn,
                    old_memory_id=old_mem,
                    new_memory_id=new_mem,
                )
                assert moved is True
                # Lookup now resolves to the new memory_id
                got = await lookup_slug(
                    conn,
                    team_id=team_id,
                    resource_type="decision",
                    slug="evolving",
                )
                assert got is not None
                assert got["memory_id"] == new_mem

    async def test_no_slug_returns_false(self: TestRedirectOnSupersession, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tid, team_id, mem = await _make_tenant_team_memory(conn)
                # No slug ever inserted
                moved = await redirect_slug_on_supersession(
                    conn,
                    old_memory_id=mem,
                    new_memory_id=mem,
                )
                assert moved is False


class TestLookupSlug:
    async def test_hit(self: TestLookupSlug, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                _, team_id, mem = await _make_tenant_team_memory(conn)
                await insert_slug_with_retry(
                    conn,
                    team_id=team_id,
                    resource_type="decision",
                    clue="lookable",
                    memory_id=mem,
                    title="T",
                )
                got = await lookup_slug(
                    conn,
                    team_id=team_id,
                    resource_type="decision",
                    slug="lookable",
                )
                assert got is not None
                assert got["memory_id"] == mem

    async def test_miss_returns_none(self: TestLookupSlug, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                _, team_id, _ = await _make_tenant_team_memory(conn)
                got = await lookup_slug(
                    conn,
                    team_id=team_id,
                    resource_type="decision",
                    slug="nonexistent",
                )
                assert got is None
