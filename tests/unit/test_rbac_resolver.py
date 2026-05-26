"""Tests for has_permission() resolver across system + team scopes."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from mem_mcp.auth.permissions import Permission
from mem_mcp.auth.rbac import has_permission


@pytest.mark.integration
class TestHasPermissionResolver:
    """Test suite for RBAC permission resolution.

    Tests use MEM_MCP_TEST_DSN env var to run against a live test DB.
    Each test inserts its own tenants, roles, and teams, then cleans up.
    Pattern matches tests/unit/test_enterprise_schema.py.
    """

    @pytest.mark.asyncio
    async def test_has_permission_system_admin_grants_system_perm(self, pg_pool: Any) -> None:
        """Insert tenant_system_roles with system_admin → has_permission returns True."""
        tenant_id = uuid4()
        async with pg_pool.acquire() as conn:
            try:
                # Insert test tenant
                await conn.execute(
                    "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                    tenant_id,
                    f"test-admin-{tenant_id}@test.invalid",
                )

                # Insert system_admin role
                await conn.execute(
                    """
                    INSERT INTO tenant_system_roles (tenant_id, role)
                    VALUES ($1, 'system_admin')
                    """,
                    tenant_id,
                )

                # Test system permissions
                result = await has_permission(
                    conn, tenant_id, Permission.SYSTEM_REVIEW_SIGNUPS
                )
                assert result is True

                result = await has_permission(
                    conn, tenant_id, Permission.SYSTEM_MANAGE_ROLES
                )
                assert result is True

                result = await has_permission(
                    conn, tenant_id, Permission.SYSTEM_APPROVE_SIGNUPS
                )
                assert result is True
            finally:
                # Cleanup
                await conn.execute(
                    "DELETE FROM tenant_system_roles WHERE tenant_id = $1", tenant_id
                )
                await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)

    @pytest.mark.asyncio
    async def test_has_permission_no_role_denies(self, pg_pool: Any) -> None:
        """Tenant with no roles → has_permission returns False."""
        tenant_id = uuid4()
        async with pg_pool.acquire() as conn:
            try:
                # Insert test tenant with NO system roles
                await conn.execute(
                    "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                    tenant_id,
                    f"test-no-role-{tenant_id}@test.invalid",
                )

                # System permission should be denied
                result = await has_permission(
                    conn, tenant_id, Permission.SYSTEM_REVIEW_SIGNUPS
                )
                assert result is False

                # Team permission should be denied (even with team_id)
                some_team_id = uuid4()
                result = await has_permission(
                    conn, tenant_id, Permission.TEAM_VIEW, team_id=some_team_id
                )
                assert result is False
            finally:
                # Cleanup
                await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)

    @pytest.mark.asyncio
    async def test_has_permission_team_admin(self, pg_pool: Any) -> None:
        """Team admin role grants team-level permissions."""
        tenant_id = uuid4()
        team_id = uuid4()
        async with pg_pool.acquire() as conn:
            try:
                # Insert tenant
                await conn.execute(
                    "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                    tenant_id,
                    f"test-team-admin-{tenant_id}@test.invalid",
                )

                # Insert team
                await conn.execute(
                    """
                    INSERT INTO teams (id, name, created_by_tenant_id)
                    VALUES ($1, $2, $3)
                    """,
                    team_id,
                    f"team-{team_id}",
                    tenant_id,
                )

                # Insert tenant as team admin (active)
                await conn.execute(
                    """
                    INSERT INTO team_members (team_id, tenant_id, role, status, added_by_tenant_id)
                    VALUES ($1, $2, 'admin', 'active', $2)
                    """,
                    team_id,
                    tenant_id,
                )

                # Should have permission when team_id matches
                result = await has_permission(
                    conn, tenant_id, Permission.TEAM_MANAGE_MEMBERS, team_id=team_id
                )
                assert result is True

                # Should NOT have permission without team_id
                result = await has_permission(
                    conn, tenant_id, Permission.TEAM_MANAGE_MEMBERS
                )
                assert result is False

                # Should NOT have permission for different team
                other_team_id = uuid4()
                result = await has_permission(
                    conn, tenant_id, Permission.TEAM_MANAGE_MEMBERS, team_id=other_team_id
                )
                assert result is False
            finally:
                # Cleanup
                await conn.execute(
                    "DELETE FROM team_members WHERE team_id = $1", team_id
                )
                await conn.execute("DELETE FROM teams WHERE id = $1", team_id)
                await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)

    @pytest.mark.asyncio
    async def test_has_permission_team_member_cannot_manage(self, pg_pool: Any) -> None:
        """Team member role grants write but not manage permissions."""
        tenant_id = uuid4()
        team_id = uuid4()
        async with pg_pool.acquire() as conn:
            try:
                # Insert tenant
                await conn.execute(
                    "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                    tenant_id,
                    f"test-team-member-{tenant_id}@test.invalid",
                )

                # Insert team
                await conn.execute(
                    """
                    INSERT INTO teams (id, name, created_by_tenant_id)
                    VALUES ($1, $2, $3)
                    """,
                    team_id,
                    f"team-{team_id}",
                    tenant_id,
                )

                # Insert tenant as team member (active, not admin)
                await conn.execute(
                    """
                    INSERT INTO team_members (team_id, tenant_id, role, status, added_by_tenant_id)
                    VALUES ($1, $2, 'member', 'active', $2)
                    """,
                    team_id,
                    tenant_id,
                )

                # Member can write memories
                result = await has_permission(
                    conn, tenant_id, Permission.TEAM_WRITE_MEMORY, team_id=team_id
                )
                assert result is True

                # Member cannot manage members
                result = await has_permission(
                    conn, tenant_id, Permission.TEAM_MANAGE_MEMBERS, team_id=team_id
                )
                assert result is False

                # Member cannot delete team
                result = await has_permission(
                    conn, tenant_id, Permission.TEAM_DELETE, team_id=team_id
                )
                assert result is False
            finally:
                # Cleanup
                await conn.execute(
                    "DELETE FROM team_members WHERE team_id = $1", team_id
                )
                await conn.execute("DELETE FROM teams WHERE id = $1", team_id)
                await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)

    @pytest.mark.asyncio
    async def test_has_permission_inactive_member_denied(self, pg_pool: Any) -> None:
        """Inactive team member (invited, not active) → team perms denied."""
        tenant_id = uuid4()
        team_id = uuid4()
        async with pg_pool.acquire() as conn:
            try:
                # Insert tenant
                await conn.execute(
                    "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                    tenant_id,
                    f"test-invited-{tenant_id}@test.invalid",
                )

                # Insert team
                await conn.execute(
                    """
                    INSERT INTO teams (id, name, created_by_tenant_id)
                    VALUES ($1, $2, $3)
                    """,
                    team_id,
                    f"team-{team_id}",
                    tenant_id,
                )

                # Insert tenant as invited (NOT active)
                await conn.execute(
                    """
                    INSERT INTO team_members (team_id, tenant_id, role, status, added_by_tenant_id)
                    VALUES ($1, $2, 'admin', 'invited', $2)
                    """,
                    team_id,
                    tenant_id,
                )

                # Team permissions should be denied (invited, not active)
                result = await has_permission(
                    conn, tenant_id, Permission.TEAM_VIEW, team_id=team_id
                )
                assert result is False

                result = await has_permission(
                    conn, tenant_id, Permission.TEAM_MANAGE_MEMBERS, team_id=team_id
                )
                assert result is False
            finally:
                # Cleanup
                await conn.execute(
                    "DELETE FROM team_members WHERE team_id = $1", team_id
                )
                await conn.execute("DELETE FROM teams WHERE id = $1", team_id)
                await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
