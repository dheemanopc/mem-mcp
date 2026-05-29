"""Tests for user_effective_team_access table schema."""

from __future__ import annotations

import asyncpg  # type: ignore[import-untyped]
import pytest

pytestmark = pytest.mark.asyncio


class TestUserEffectiveTeamAccessSchema:
    async def test_table_exists(self, pg_pool: asyncpg.pool.Pool) -> None:
        async with pg_pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'user_effective_team_access')"
            )
            assert exists is True

    async def test_effective_role_class_check_constraint(self, pg_pool: asyncpg.pool.Pool) -> None:
        """effective_role_class must be in {internal, external, service}."""
        from uuid import uuid4

        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('uea-test', $1) RETURNING id",
                    tenant_id,
                )
                # Valid insert
                await conn.execute(
                    """INSERT INTO user_effective_team_access
                       (user_id, resource_team_id, effective_role_class, effective_perms, path_count)
                       VALUES ($1, $2, 'internal', ARRAY['read'], 1)""",
                    tenant_id,
                    team_id,
                )
                # Invalid class → IntegrityError
                with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                    await conn.execute(
                        """INSERT INTO user_effective_team_access
                           (user_id, resource_team_id, effective_role_class, effective_perms, path_count)
                           VALUES ($1, $2, 'admin', ARRAY['read'], 1)""",
                        tenant_id,
                        team_id,
                    )

    async def test_path_count_must_be_positive(self, pg_pool: asyncpg.pool.Pool) -> None:
        from uuid import uuid4

        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('uea-pc', $1) RETURNING id",
                    tenant_id,
                )
                with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                    await conn.execute(
                        """INSERT INTO user_effective_team_access
                           (user_id, resource_team_id, effective_role_class, effective_perms, path_count)
                           VALUES ($1, $2, 'internal', ARRAY['read'], 0)""",
                        tenant_id,
                        team_id,
                    )

    async def test_pk_uniqueness(self, pg_pool: asyncpg.pool.Pool) -> None:
        from uuid import uuid4

        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('uea-pk', $1) RETURNING id",
                    tenant_id,
                )
                await conn.execute(
                    """INSERT INTO user_effective_team_access
                       (user_id, resource_team_id, effective_role_class, effective_perms, path_count)
                       VALUES ($1, $2, 'internal', ARRAY['read'], 1)""",
                    tenant_id,
                    team_id,
                )
                with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                    await conn.execute(
                        """INSERT INTO user_effective_team_access
                           (user_id, resource_team_id, effective_role_class, effective_perms, path_count)
                           VALUES ($1, $2, 'external', ARRAY['read'], 1)""",
                        tenant_id,
                        team_id,
                    )

    async def test_cascade_delete_on_user(self, pg_pool: asyncpg.pool.Pool) -> None:
        from uuid import uuid4

        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_id = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                team_id = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('uea-cd', $1) RETURNING id",
                    tenant_id,
                )
                await conn.execute(
                    """INSERT INTO user_effective_team_access
                       (user_id, resource_team_id, effective_role_class, effective_perms, path_count)
                       VALUES ($1, $2, 'internal', ARRAY['read'], 1)""",
                    tenant_id,
                    team_id,
                )
                # Delete tenant → CASCADE removes uea row
                await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
                cnt = await conn.fetchval(
                    "SELECT count(*) FROM user_effective_team_access WHERE user_id = $1",
                    tenant_id,
                )
                assert cnt == 0
