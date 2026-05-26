"""Tests for has_permission() resolver across system + team scopes."""
from __future__ import annotations

import pytest


@pytest.mark.skipif(
    True,
    reason="Requires live DB fixture infrastructure (system_tx + multi-tenant inserts) — see notes below",
)
class TestHasPermissionResolver:
    """Test suite for RBAC permission resolution.

    NOTE: Full resolver tests require a live-DB fixture that can:
      1. Insert test tenants + identities
      2. Insert tenant_system_roles rows
      3. Insert team_members rows with active status
      4. Provide a system_tx connection for queries

    This fixture infrastructure does not yet exist in the test suite.
    To implement: extend tests/conftest.py with:
      - setup_system_roles(pg_pool, tenant_id, roles) fixture
      - setup_team_members(pg_pool, team_id, tenant_id, role) fixture
      - A resolver_test_context fixture that provides conn + tenant setup

    Tests below are template specs awaiting fixture implementation.
    """

    @pytest.mark.asyncio
    async def test_has_permission_system_admin_grants_system_perm(self) -> None:
        """Insert tenant_system_roles with system_admin → has_permission returns True."""
        # fixture: conn, tenant_id with system_admin role
        # await has_permission(conn, tenant_id, Permission.SYSTEM_REVIEW_SIGNUPS)
        # assert result is True
        pass

    @pytest.mark.asyncio
    async def test_has_permission_no_role_denies(self) -> None:
        """Tenant with no roles → has_permission returns False."""
        # fixture: conn, tenant_id with no roles
        # await has_permission(conn, tenant_id, Permission.SYSTEM_REVIEW_SIGNUPS)
        # assert result is False
        pass

    @pytest.mark.asyncio
    async def test_has_permission_team_perm_requires_team_id(self) -> None:
        """Team perm check: returns True with correct team_id, False without."""
        # fixture: conn, tenant_id, team_id where tenant is team admin
        # with team_id: has_permission(perm=TEAM_VIEW) -> True
        # without team_id: has_permission(perm=TEAM_VIEW) -> False
        pass

    @pytest.mark.asyncio
    async def test_has_permission_team_perm_wrong_team(self) -> None:
        """Caller is admin of team A, ask about team B → False."""
        # fixture: conn, tenant_id, team_a_id (caller is admin), team_b_id (not member)
        # has_permission(team_id=team_b_id, perm=TEAM_VIEW) -> False
        pass
