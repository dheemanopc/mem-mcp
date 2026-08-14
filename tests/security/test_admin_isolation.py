"""Security: the admin tool family must not be reachable without a system role.

Marked `security` so the nightly gate (`pytest tests/security -m security`)
picks these up. The threat model is specific: these tools are callable by an
LLM over MCP and they read across every tenant on the instance, so a hole here
is a cross-tenant data leak rather than a privilege nuisance.

The DB-backed permission-gate coverage lives in
tests/integration/test_admin_tools.py, which runs in the PR gate. What is here
is the isolation-specific half.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from mem_mcp.auth.permissions import ROLE_PERMISSIONS, Permission
from mem_mcp.db import system_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools.admin_find_user import AdminFindUserInput, AdminFindUserTool

pytestmark = pytest.mark.security


class TestTeamAdminIsNotSystemAdmin:
    """Team-level 'admin' must never confer system-level admin."""

    def test_team_roles_hold_no_system_permissions(self) -> None:
        """The role matrix keeps the namespaces disjoint."""
        for role_key in ("admin", "member"):
            perms = ROLE_PERMISSIONS[role_key]
            assert Permission.SYSTEM_MANAGE_TENANTS not in perms
            assert not any(p.value.startswith("system.") for p in perms)

    async def test_team_owner_cannot_enumerate_users(self, pg_pool: Any) -> None:
        """Owning a team does not let you list the instance's users.

        The caller here is the creator/owner of a team — the strongest
        team-scoped role available — and must still be refused.
        """
        from mem_mcp.teams.assignments import assign_role

        tenant_id = uuid4()
        identity_id = uuid4()
        team_id = uuid4()
        email = f"teamowner-{tenant_id}@sec-admin.invalid"

        async with system_tx(pg_pool) as conn:
            await conn.execute(
                "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                tenant_id,
                email,
            )
            await conn.execute(
                """
                INSERT INTO tenant_identities
                    (id, tenant_id, cognito_sub, provider, email, is_primary)
                VALUES ($1, $2, $3, 'cognito', $4, true)
                """,
                identity_id,
                tenant_id,
                f"sub-{tenant_id}",
                email,
            )
            await conn.execute(
                """
                INSERT INTO teams (id, name, workspace_domain, created_by_tenant_id)
                VALUES ($1, 'sec-probe', NULL, $2)
                """,
                team_id,
                tenant_id,
            )
            await assign_role(
                conn,
                parent_team_id=team_id,
                member_kind="user",
                member_id=tenant_id,
                role_key="owner",
                assigned_by_user_id=tenant_id,
            )

        ctx = ToolContext(
            request_id=str(uuid4()),
            tenant_id=tenant_id,
            identity_id=identity_id,
            client_id="sec-admin",
            scopes=frozenset({"memory.read", "memory.write"}),
            db_pool=pg_pool,
        )

        tool = AdminFindUserTool()
        with pytest.raises(JsonRpcError) as exc:
            await tool(ctx, AdminFindUserInput(query="teamowner-"))
        assert exc.value.data is not None
        assert exc.value.data["missing_permission"] == Permission.SYSTEM_MANAGE_TENANTS.value

    async def test_denial_is_an_error_not_an_empty_list(self, pg_pool: Any) -> None:
        """Returning [] to an unprivileged caller would be an existence oracle."""
        tenant_id = uuid4()
        identity_id = uuid4()
        email = f"nobody-{tenant_id}@sec-admin.invalid"

        async with system_tx(pg_pool) as conn:
            await conn.execute(
                "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                tenant_id,
                email,
            )
            await conn.execute(
                """
                INSERT INTO tenant_identities
                    (id, tenant_id, cognito_sub, provider, email, is_primary)
                VALUES ($1, $2, $3, 'cognito', $4, true)
                """,
                identity_id,
                tenant_id,
                f"sub-{tenant_id}",
                email,
            )

        ctx = ToolContext(
            request_id=str(uuid4()),
            tenant_id=tenant_id,
            identity_id=identity_id,
            client_id="sec-admin",
            scopes=frozenset({"memory.read"}),
            db_pool=pg_pool,
        )

        with pytest.raises(JsonRpcError):
            await AdminFindUserTool()(ctx, AdminFindUserInput(query="nobody-"))
