"""Tests for memory_references table schema + constraints."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from asyncpg import Connection  # type: ignore[import-untyped]
    from asyncpg.pool import Pool  # type: ignore[import-untyped]

import asyncpg

pytestmark = pytest.mark.asyncio


async def _make_two_memories(conn: Connection) -> tuple[str, str, str, str]:
    """Helper: create tenant + team + two memories in same team."""
    tenant_id = await conn.fetchval(
        "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
        f"test-{uuid4()}@example.test",
    )
    team_id = await conn.fetchval(
        "INSERT INTO teams (name, created_by_tenant_id) VALUES ($1, $2) RETURNING id",
        f"ref-{uuid4().hex[:8]}",
        tenant_id,
    )
    m1 = await conn.fetchval(
        "INSERT INTO memories (tenant_id, content, content_hash, embedding, source_kind, type) "
        "VALUES ($1, 'c1', $2, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision') RETURNING id",
        tenant_id,
        f"hash-{uuid4().hex}",
    )
    m2 = await conn.fetchval(
        "INSERT INTO memories (tenant_id, content, content_hash, embedding, source_kind, type) "
        "VALUES ($1, 'c2', $2, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision') RETURNING id",
        tenant_id,
        f"hash-{uuid4().hex}",
    )
    return tenant_id, team_id, m1, m2


class TestMemoryReferencesSchema:
    async def test_table_exists(self, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'memory_references')"
            )
            assert exists is True

    async def test_insert_valid_reference(self, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                _, team_id, src, tgt = await _make_two_memories(conn)
                await conn.execute(
                    """
                    INSERT INTO memory_references (source_memory_id, target_memory_id, target_team_id, reference_kind)
                    VALUES ($1, $2, $3, 'cites')
                    """,
                    src,
                    tgt,
                    team_id,
                )
                cnt = await conn.fetchval(
                    "SELECT count(*) FROM memory_references WHERE source_memory_id = $1",
                    src,
                )
                assert cnt == 1

    async def test_default_refs_version_is_pinned(self, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                _, team_id, src, tgt = await _make_two_memories(conn)
                await conn.execute(
                    "INSERT INTO memory_references (source_memory_id, target_memory_id, target_team_id, reference_kind) "
                    "VALUES ($1, $2, $3, 'cites')",
                    src,
                    tgt,
                    team_id,
                )
                row = await conn.fetchrow(
                    "SELECT refs_version FROM memory_references WHERE source_memory_id = $1",
                    src,
                )
                assert row["refs_version"] == "pinned"

    async def test_refs_version_check_constraint(self, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                _, team_id, src, tgt = await _make_two_memories(conn)
                with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                    await conn.execute(
                        "INSERT INTO memory_references (source_memory_id, target_memory_id, target_team_id, reference_kind, refs_version) "
                        "VALUES ($1, $2, $3, 'cites', 'invalid')",
                        src,
                        tgt,
                        team_id,
                    )

    async def test_pk_includes_fragment_and_kind(self, pg_pool: Pool) -> None:
        """Same (source, target) with different kind or fragment is allowed."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                _, team_id, src, tgt = await _make_two_memories(conn)
                await conn.execute(
                    "INSERT INTO memory_references (source_memory_id, target_memory_id, target_team_id, reference_kind) "
                    "VALUES ($1, $2, $3, 'cites')",
                    src,
                    tgt,
                    team_id,
                )
                # Different kind → OK
                await conn.execute(
                    "INSERT INTO memory_references (source_memory_id, target_memory_id, target_team_id, reference_kind) "
                    "VALUES ($1, $2, $3, 'derived-from')",
                    src,
                    tgt,
                    team_id,
                )
                # Same (src, tgt, kind, null fragment) again → duplicate PK
                with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                    await conn.execute(
                        "INSERT INTO memory_references (source_memory_id, target_memory_id, target_team_id, reference_kind) "
                        "VALUES ($1, $2, $3, 'cites')",
                        src,
                        tgt,
                        team_id,
                    )

    async def test_hard_delete_target_blocked_by_fk(self, pg_pool: Pool) -> None:
        """ON DELETE RESTRICT on target_memory_id: cannot hard-delete a memory with inbound refs."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                _, team_id, src, tgt = await _make_two_memories(conn)
                await conn.execute(
                    "INSERT INTO memory_references (source_memory_id, target_memory_id, target_team_id, reference_kind) "
                    "VALUES ($1, $2, $3, 'cites')",
                    src,
                    tgt,
                    team_id,
                )
                with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                    await conn.execute("DELETE FROM memories WHERE id = $1", tgt)

    async def test_cascade_delete_source(self, pg_pool: Pool) -> None:
        """ON DELETE CASCADE on source_memory_id: deleting source drops its refs."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                _, team_id, src, tgt = await _make_two_memories(conn)
                await conn.execute(
                    "INSERT INTO memory_references (source_memory_id, target_memory_id, target_team_id, reference_kind) "
                    "VALUES ($1, $2, $3, 'cites')",
                    src,
                    tgt,
                    team_id,
                )
                # Cascade delete source (but only after dropping the inbound-target guard on src)
                # Src has no inbound refs → DELETE allowed; refs cascade away.
                await conn.execute("DELETE FROM memories WHERE id = $1", src)
                cnt = await conn.fetchval(
                    "SELECT count(*) FROM memory_references WHERE source_memory_id = $1",
                    src,
                )
                assert cnt == 0
