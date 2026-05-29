"""Tests for team_role_assignments schema + triggers (polymorphic FK + parent_count)."""

from __future__ import annotations

from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest

pytestmark = pytest.mark.asyncio


class TestPolymorphicFKTrigger:
    async def test_user_kind_requires_tenant_existence(self, pg_pool: object) -> None:
        async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
            # Get a real team_id + a real role_id
            team_row = await conn.fetchrow("SELECT id FROM teams LIMIT 1")
            if team_row is None:
                pytest.skip("no teams in test db")
            role_id = await conn.fetchval(
                "SELECT id FROM roles_catalog WHERE role_key = 'member' AND plugin_id IS NULL"
            )
            tenant_row = await conn.fetchrow("SELECT id FROM tenants LIMIT 1")
            if tenant_row is None:
                pytest.skip("no tenants in test db")

            # member_kind='user' with non-existent member_id → FK violation
            async with conn.transaction():
                with pytest.raises(asyncpg.PostgresError):
                    await conn.execute(
                        """
                        INSERT INTO team_role_assignments
                            (parent_team_id, member_kind, member_id, role_id, assigned_by_user_id)
                        VALUES ($1, 'user', $2, $3, $4)
                    """,
                        team_row["id"],
                        uuid4(),
                        role_id,
                        tenant_row["id"],
                    )

    async def test_team_kind_requires_team_existence(self, pg_pool: object) -> None:
        async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
            team_row = await conn.fetchrow("SELECT id FROM teams LIMIT 1")
            if team_row is None:
                pytest.skip("no teams in test db")
            role_id = await conn.fetchval(
                "SELECT id FROM roles_catalog WHERE role_key = 'member' AND plugin_id IS NULL"
            )
            tenant_row = await conn.fetchrow("SELECT id FROM tenants LIMIT 1")
            if tenant_row is None:
                pytest.skip("no tenants in test db")

            async with conn.transaction():
                with pytest.raises(asyncpg.PostgresError):
                    await conn.execute(
                        """
                        INSERT INTO team_role_assignments
                            (parent_team_id, member_kind, member_id, role_id, assigned_by_user_id)
                        VALUES ($1, 'team', $2, $3, $4)
                    """,
                        team_row["id"],
                        uuid4(),
                        role_id,
                        tenant_row["id"],
                    )


class TestParentCountTrigger:
    async def test_parent_count_increments_on_team_member_insert(self, pg_pool: object) -> None:
        async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
            async with conn.transaction():
                # Create two teams + a tenant for assigned_by
                tenant_row = await conn.fetchrow("SELECT id FROM tenants LIMIT 1")
                if tenant_row is None:
                    pytest.skip("no tenants")
                role_id = await conn.fetchval(
                    "SELECT id FROM roles_catalog WHERE role_key = 'member' AND plugin_id IS NULL"
                )
                parent = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('test-parent', $1) RETURNING id",
                    tenant_row["id"],
                )
                child = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('test-child', $1) RETURNING id",
                    tenant_row["id"],
                )
                child_before = await conn.fetchval(
                    "SELECT parent_count FROM teams WHERE id = $1", child
                )
                assert child_before == 0

                # Add child as member of parent
                await conn.execute(
                    """
                    INSERT INTO team_role_assignments
                        (parent_team_id, member_kind, member_id, role_id, assigned_by_user_id)
                    VALUES ($1, 'team', $2, $3, $4)
                """,
                    parent,
                    child,
                    role_id,
                    tenant_row["id"],
                )

                child_after = await conn.fetchval(
                    "SELECT parent_count FROM teams WHERE id = $1", child
                )
                assert child_after == 1

                # Remove
                await conn.execute(
                    """
                    DELETE FROM team_role_assignments
                    WHERE parent_team_id = $1 AND member_kind = 'team' AND member_id = $2
                """,
                    parent,
                    child,
                )

                child_final = await conn.fetchval(
                    "SELECT parent_count FROM teams WHERE id = $1", child
                )
                assert child_final == 0
                # Tx auto-rolls-back; no cleanup needed.


class TestBackfillFromTeamMembers:
    async def test_existing_team_members_backfilled(self, pg_pool: object) -> None:
        """Migration 0022 should have already backfilled rows from team_members."""
        async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
            tm_count = await conn.fetchval("SELECT count(*) FROM team_members")
            tra_user_count = await conn.fetchval(
                "SELECT count(*) FROM team_role_assignments WHERE member_kind = 'user'"
            )
            # Every team_members row should have an equivalent team_role_assignments row
            assert tra_user_count >= tm_count

    async def test_role_key_mapped_to_role_id(self, pg_pool: object) -> None:
        async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
            # For any team_members row with role='admin', the matching team_role_assignments
            # row should have role_id pointing at the admin role in roles_catalog.
            row = await conn.fetchrow("""
                SELECT tm.team_id, tm.tenant_id, tm.role,
                       (SELECT rc.role_key FROM team_role_assignments tra
                        JOIN roles_catalog rc ON rc.id = tra.role_id
                        WHERE tra.parent_team_id = tm.team_id
                          AND tra.member_kind = 'user'
                          AND tra.member_id = tm.tenant_id
                       ) AS migrated_role_key
                FROM team_members tm
                LIMIT 1
            """)
            if row is None:
                pytest.skip("no team_members rows")
            assert row["role"] == row["migrated_role_key"]
