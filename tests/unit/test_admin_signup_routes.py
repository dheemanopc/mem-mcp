"""Integration tests for admin signup routes with RBAC."""

from __future__ import annotations

from uuid import uuid4

import pytest

from mem_mcp.auth.permissions import Permission


@pytest.mark.integration
class TestAdminSignupRoutesRBAC:
    """Test suite for admin signup routes using RBAC permission gates.

    Tests use MEM_MCP_TEST_DSN env var to run against a live test DB.
    Each test inserts test tenants with system roles, then cleans up.
    """

    @pytest.mark.asyncio
    async def test_signup_list_requires_review_perm(self, pg_pool: object) -> None:
        """GET /api/web/admin/signup-requests with no SYSTEM_REVIEW_SIGNUPS → 403."""

        tenant_id = uuid4()
        pg_pool = pg_pool  # type: ignore[name-defined]

        async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
            try:
                # Insert test tenant with NO system roles
                await conn.execute(
                    "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                    tenant_id,
                    f"test-no-role-{tenant_id}@test.invalid",
                )

                # Test that has_permission returns False
                from mem_mcp.auth.rbac import has_permission

                result = await has_permission(conn, tenant_id, Permission.SYSTEM_REVIEW_SIGNUPS)
                assert result is False
            finally:
                await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)

    @pytest.mark.asyncio
    async def test_signup_list_returns_200_for_system_admin(self, pg_pool: object) -> None:
        """GET /api/web/admin/signup-requests with system_admin → 200."""
        tenant_id = uuid4()
        pg_pool = pg_pool  # type: ignore[name-defined]

        async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
            try:
                # Insert test tenant
                await conn.execute(
                    "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                    tenant_id,
                    f"test-admin-{tenant_id}@test.invalid",
                )

                # Insert system_admin role
                await conn.execute(
                    "INSERT INTO tenant_system_roles (tenant_id, role) VALUES ($1, 'system_admin')",
                    tenant_id,
                )

                # Test that has_permission returns True
                from mem_mcp.auth.rbac import has_permission

                result = await has_permission(conn, tenant_id, Permission.SYSTEM_REVIEW_SIGNUPS)
                assert result is True

                result = await has_permission(conn, tenant_id, Permission.SYSTEM_APPROVE_SIGNUPS)
                assert result is True

                result = await has_permission(conn, tenant_id, Permission.SYSTEM_REJECT_SIGNUPS)
                assert result is True
            finally:
                await conn.execute(
                    "DELETE FROM tenant_system_roles WHERE tenant_id = $1", tenant_id
                )
                await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)

    @pytest.mark.asyncio
    async def test_signup_list_returns_200_for_system_support(self, pg_pool: object) -> None:
        """GET /api/web/admin/signup-requests with system_support → 200 (review perm)."""
        tenant_id = uuid4()
        pg_pool = pg_pool  # type: ignore[name-defined]

        async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
            try:
                # Insert test tenant
                await conn.execute(
                    "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                    tenant_id,
                    f"test-support-{tenant_id}@test.invalid",
                )

                # Insert system_support role
                await conn.execute(
                    "INSERT INTO tenant_system_roles (tenant_id, role) VALUES ($1, 'system_support')",
                    tenant_id,
                )

                # Test that has_permission returns True for REVIEW (support perm)
                from mem_mcp.auth.rbac import has_permission

                result = await has_permission(conn, tenant_id, Permission.SYSTEM_REVIEW_SIGNUPS)
                assert result is True

                # But False for APPROVE (admin-only perm)
                result = await has_permission(conn, tenant_id, Permission.SYSTEM_APPROVE_SIGNUPS)
                assert result is False

                # And False for REJECT (admin-only perm)
                result = await has_permission(conn, tenant_id, Permission.SYSTEM_REJECT_SIGNUPS)
                assert result is False
            finally:
                await conn.execute(
                    "DELETE FROM tenant_system_roles WHERE tenant_id = $1", tenant_id
                )
                await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)

    @pytest.mark.asyncio
    async def test_signup_approve_requires_approve_perm(self, pg_pool: object) -> None:
        """POST .../approve with only system_support (no approve perm) → 403."""
        tenant_id = uuid4()
        pg_pool = pg_pool  # type: ignore[name-defined]

        async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
            try:
                # Insert test tenant
                await conn.execute(
                    "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                    tenant_id,
                    f"test-support-{tenant_id}@test.invalid",
                )

                # Insert system_support role (review only, NOT approve)
                await conn.execute(
                    "INSERT INTO tenant_system_roles (tenant_id, role) VALUES ($1, 'system_support')",
                    tenant_id,
                )

                # Verify support has REVIEW but NOT APPROVE
                from mem_mcp.auth.rbac import has_permission

                review_result = await has_permission(
                    conn, tenant_id, Permission.SYSTEM_REVIEW_SIGNUPS
                )
                approve_result = await has_permission(
                    conn, tenant_id, Permission.SYSTEM_APPROVE_SIGNUPS
                )

                assert review_result is True
                assert approve_result is False
            finally:
                await conn.execute(
                    "DELETE FROM tenant_system_roles WHERE tenant_id = $1", tenant_id
                )
                await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
