"""Tests for migration 0026: personal teams backfill + visibility widen + fragment_id."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import asyncpg
import pytest

if TYPE_CHECKING:
    from asyncpg import Pool  # type: ignore[import-untyped]


pytestmark = pytest.mark.asyncio


class TestVisibilityWiden:
    async def test_team_value_accepted(self, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                t_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"vw-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id, team_type) VALUES ('vw-team', $1, 'personal') RETURNING id",
                    t_id,
                )
                mem = await conn.fetchval(
                    """
                    INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type, visibility)
                    VALUES ($1, $2, 'c', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'note', 'team')
                    RETURNING id
                    """,
                    t_id,
                    team_id,
                    f"hash-{uuid4().hex}",
                )
                assert mem is not None

    async def test_external_value_accepted(self, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                t_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"vw-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id, team_type) VALUES ('vw-e', $1, 'personal') RETURNING id",
                    t_id,
                )
                mem = await conn.fetchval(
                    "INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type, visibility) "
                    "VALUES ($1, $2, 'c', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'note', 'external') RETURNING id",
                    t_id,
                    team_id,
                    f"hash-{uuid4().hex}",
                )
                assert mem is not None

    async def test_public_value_accepted(self, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                t_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"vw-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id, team_type) VALUES ('vw-p', $1, 'personal') RETURNING id",
                    t_id,
                )
                mem = await conn.fetchval(
                    "INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type, visibility) "
                    "VALUES ($1, $2, 'c', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'note', 'public') RETURNING id",
                    t_id,
                    team_id,
                    f"hash-{uuid4().hex}",
                )
                assert mem is not None

    async def test_private_value_rejected(self, pg_pool: Pool) -> None:
        """Old 'private' value must be rejected by the new CHECK."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                t_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"vw-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id, team_type) VALUES ('vw-pp', $1, 'personal') RETURNING id",
                    t_id,
                )
                with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                    await conn.execute(
                        "INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type, visibility) "
                        "VALUES ($1, $2, 'c', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'note', 'private')",
                        t_id,
                        team_id,
                        f"hash-{uuid4().hex}",
                    )

    async def test_visibility_default_is_team(self, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                t_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"vw-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id, team_type) VALUES ('vw-d', $1, 'personal') RETURNING id",
                    t_id,
                )
                mem_id = await conn.fetchval(
                    "INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type) "
                    "VALUES ($1, $2, 'c', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'note') RETURNING id",
                    t_id,
                    team_id,
                    f"hash-{uuid4().hex}",
                )
                vis = await conn.fetchval("SELECT visibility FROM memories WHERE id = $1", mem_id)
                assert vis == "team"


class TestFragmentIdColumn:
    async def test_column_exists_nullable(self, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT is_nullable, data_type FROM information_schema.columns "
                "WHERE table_name = 'memories' AND column_name = 'fragment_id'"
            )
            assert row is not None
            assert row["is_nullable"] == "YES"
            assert row["data_type"] == "integer"

    async def test_partial_index_exists(self, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM pg_indexes WHERE indexname = 'memories_parent_fragment_idx')"
            )
            assert exists is True

    async def test_set_fragment_id_for_reply(self, pg_pool: Pool) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                t_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"f-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id, team_type) VALUES ('f-team', $1, 'personal') RETURNING id",
                    t_id,
                )
                root = await conn.fetchval(
                    "INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type) "
                    "VALUES ($1, $2, 'root', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision') RETURNING id",
                    t_id,
                    team_id,
                    f"hash-{uuid4().hex}",
                )
                reply = await conn.fetchval(
                    "INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type, parent_id, fragment_id) "
                    "VALUES ($1, $2, 'reply', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'note', $4, 1) RETURNING id",
                    t_id,
                    team_id,
                    f"hash-{uuid4().hex}",
                    root,
                )
                row = await conn.fetchrow(
                    "SELECT parent_id, fragment_id FROM memories WHERE id = $1", reply
                )
                assert row["parent_id"] == root
                assert row["fragment_id"] == 1
