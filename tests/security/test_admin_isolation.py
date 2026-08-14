"""Security: the admin tool family must not be reachable without system roles.

These gate CI and must never be skipped (GUIDELINES §2). The threat model is
specific: these tools are callable by an LLM over MCP, and they read across
every tenant on the instance. A hole here is a cross-tenant data leak, not a
privilege nuisance.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from mem_mcp.auth.permissions import ROLE_PERMISSIONS, Permission
from mem_mcp.db import system_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools.admin_find_user import AdminFindUserInput, AdminFindUserTool
from mem_mcp.mcp.tools.admin_get_user import AdminGetUserInput, AdminGetUserTool


class TestTeamAdminIsNotSystemAdmin:
    """Team-level 'admin' must not confer system-level admin."""

    def test_team_admin_role_lacks_system_permissions(self) -> None:
        """The role matrix itself must keep the namespaces disjoint."""
        team_admin_perms = ROLE_PERMISSIONS["admin"]
        assert Permission.SYSTEM_MANAGE_TENANTS not in team_admin_perms
        assert not any(p.value.startswith("system.") for p in team_admin_perms)

    async def test_team_admin_cannot_call_find_user(
        self, tool_ctx_with_workspace: Any, pg_pool: Any
    ) -> None:
        """Owning a team does not let you enumerate the instance's users."""
        async with system_tx(pg_pool) as conn:
            team_id = uuid4()
            # teams.workspace_domain is globally unique, so an earlier test in
            # the session may already own the example.com team. Either way the
            # caller ends up with a team-level role and no system role, which
            # is the condition under test.
            await conn.execute(
                """
                INSERT INTO teams (id, name, workspace_domain, created_by_tenant_id)
                VALUES ($1, 'sec-probe', 'example.com', $2)
                ON CONFLICT DO NOTHING
                """,
                team_id,
                tool_ctx_with_workspace.tenant_id,
            )

        tool = AdminFindUserTool()
        with pytest.raises(JsonRpcError) as exc:
            await tool(tool_ctx_with_workspace, AdminFindUserInput(query="workspace-"))
        assert exc.value.data is not None
        assert exc.value.data["missing_permission"] == Permission.SYSTEM_MANAGE_TENANTS.value


class TestNoExistenceOracle:
    """A denied caller must not learn whether a record exists."""

    async def test_find_user_denies_rather_than_returning_empty(
        self, tool_ctx_personal: Any
    ) -> None:
        """Denial must be an error, not an empty list.

        An empty list is indistinguishable from 'no match', which would let an
        unprivileged caller probe for the existence of accounts.
        """
        tool = AdminFindUserTool()
        with pytest.raises(JsonRpcError):
            await tool(tool_ctx_personal, AdminFindUserInput(query="anything"))

    async def test_get_user_denies_before_checking_existence(self, tool_ctx_personal: Any) -> None:
        """Denial must not depend on whether the tenant_id is real.

        Both a real and a fabricated id must fail the same way, otherwise the
        error code itself is an oracle.
        """
        tool = AdminGetUserTool()

        with pytest.raises(JsonRpcError) as real:
            await tool(tool_ctx_personal, AdminGetUserInput(tenant_id=tool_ctx_personal.tenant_id))
        with pytest.raises(JsonRpcError) as fake:
            await tool(tool_ctx_personal, AdminGetUserInput(tenant_id=uuid4()))

        assert real.value.code == fake.value.code
        assert real.value.data == fake.value.data


class TestSqlInjectionProbes:
    """Operator-supplied search text is untrusted."""

    @pytest.mark.parametrize(
        "probe",
        [
            "' OR '1'='1",
            "'; DROP TABLE tenants; --",
            "%' --",
            "\\\\",
        ],
    )
    async def test_probes_do_not_escape_the_parameter(
        self, tool_ctx_personal: Any, pg_pool: Any, probe: str
    ) -> None:
        """Each probe must be treated as literal text, and the table must survive."""
        from mem_mcp.admin.service import find_users

        async with system_tx(pg_pool) as conn:
            rows = await find_users(conn, query=probe)
            assert rows == []
            # The table is still there — proves nothing was executed.
            assert await conn.fetchval("SELECT COUNT(*) FROM tenants") >= 1
