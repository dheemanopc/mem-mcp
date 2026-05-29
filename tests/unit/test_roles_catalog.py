"""Tests for roles_catalog table + seed."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio


class TestRolesCatalogSeed:
    async def test_seed_inserts_7_core_roles(self, pg_pool: Any) -> None:
        """7 core roles present after migration."""
        async with pg_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM roles_catalog WHERE plugin_id IS NULL"
            )
            assert count == 7

    async def test_seed_idempotent_on_rerun(self, pg_pool: Any) -> None:
        """Re-running the seed INSERT (with ON CONFLICT DO NOTHING) produces no duplicates."""
        async with pg_pool.acquire() as conn:
            # Get baseline
            before = await conn.fetchval(
                "SELECT count(*) FROM roles_catalog WHERE plugin_id IS NULL"
            )
            # Re-run the seed SQL
            await conn.execute("""
                INSERT INTO roles_catalog (role_key, display_name, description, role_class, permissions) VALUES
                ('owner', 'Owner', 'x', 'internal', ARRAY['read'])
                ON CONFLICT (plugin_id, role_key) DO NOTHING
            """)
            after = await conn.fetchval(
                "SELECT count(*) FROM roles_catalog WHERE plugin_id IS NULL"
            )
            assert after == before

    async def test_owner_has_delete_admin_does_not(self, pg_pool: Any) -> None:
        async with pg_pool.acquire() as conn:
            owner = await conn.fetchrow(
                "SELECT permissions FROM roles_catalog WHERE role_key='owner' AND plugin_id IS NULL"
            )
            admin = await conn.fetchrow(
                "SELECT permissions FROM roles_catalog WHERE role_key='admin' AND plugin_id IS NULL"
            )
            assert "delete" in owner["permissions"]
            assert "delete" not in admin["permissions"]


class TestRolesCatalogConstraints:
    async def test_role_class_check_constraint(self, pg_pool: Any) -> None:
        import asyncpg  # type: ignore[import-untyped]

        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                with pytest.raises(asyncpg.IntegrityError):
                    await conn.execute(
                        "INSERT INTO roles_catalog (role_key, display_name, role_class) "
                        "VALUES ('bogus', 'Bogus', 'invalid_class')"
                    )

    async def test_role_key_unique_within_plugin_scope(self, pg_pool: Any) -> None:
        """Same role_key with plugin_id=NULL AND plugin_id='pm' both succeed."""
        async with pg_pool.acquire() as conn:
            # Cleanup any prior test row
            await conn.execute("DELETE FROM roles_catalog WHERE plugin_id = 'test-plugin'")
            await conn.execute(
                "INSERT INTO roles_catalog (role_key, display_name, role_class, plugin_id) "
                "VALUES ('owner', 'Plugin Owner', 'internal', 'test-plugin')"
            )
            count = await conn.fetchval(
                "SELECT count(*) FROM roles_catalog WHERE role_key = 'owner'"
            )
            assert count >= 2  # core owner + plugin owner
            # Cleanup
            await conn.execute("DELETE FROM roles_catalog WHERE plugin_id = 'test-plugin'")
