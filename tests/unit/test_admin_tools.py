"""Pure-logic tests for the memsys_admin_* tool family (admin-over-MCP, PR 1).

Deliberately DB-free so they run in the `unit` CI job, which has no Postgres
service and therefore skips anything gated on `pg_pool`. The DB-backed half
lives in tests/integration/test_admin_tools.py, where CI does provide a
database — otherwise the permission-gate tests would silently skip in the very
gate that is supposed to enforce them.
"""

from __future__ import annotations

from mem_mcp.admin.service import _clamp_limit, _escape_like
from mem_mcp.auth.permissions import ROLE_PERMISSIONS, Permission
from mem_mcp.mcp.tools._admin_base import AdminTool
from mem_mcp.mcp.tools.admin_find_user import AdminFindUserTool
from mem_mcp.mcp.tools.admin_get_user import AdminGetUserTool

ADMIN_TOOLS = (AdminFindUserTool, AdminGetUserTool)


class TestGateCannotBeBypassed:
    """Structural guarantees — the gate lives in AdminTool.__call__."""

    def test_no_admin_tool_overrides_call(self) -> None:
        """Overriding __call__ would skip the permission check entirely."""
        for tool_cls in ADMIN_TOOLS:
            assert (
                "__call__" not in tool_cls.__dict__
            ), f"{tool_cls.__name__} overrides __call__ and bypasses the permission gate"

    def test_every_admin_tool_declares_a_system_permission(self) -> None:
        """A tool with no required_permission would gate on nothing."""
        for tool_cls in ADMIN_TOOLS:
            assert issubclass(tool_cls, AdminTool)
            perm = tool_cls.required_permission
            assert isinstance(perm, Permission)
            assert perm.value.startswith(
                "system."
            ), f"{tool_cls.__name__} gates on {perm.value}, which is not a system permission"

    def test_every_admin_tool_declares_an_audit_action(self) -> None:
        for tool_cls in ADMIN_TOOLS:
            assert tool_cls.audit_action.startswith("admin.")


class TestTeamRolesAreNotSystemRoles:
    """The role matrix must keep the two namespaces disjoint."""

    def test_team_admin_lacks_system_permissions(self) -> None:
        team_admin_perms = ROLE_PERMISSIONS["admin"]
        assert Permission.SYSTEM_MANAGE_TENANTS not in team_admin_perms
        assert not any(p.value.startswith("system.") for p in team_admin_perms)

    def test_team_member_lacks_system_permissions(self) -> None:
        assert not any(p.value.startswith("system.") for p in ROLE_PERMISSIONS["member"])


class TestEscapeLike:
    """LIKE-wildcard neutralisation on operator-supplied search text."""

    def test_percent_becomes_literal(self) -> None:
        assert _escape_like("100%") == r"100\%"

    def test_underscore_becomes_literal(self) -> None:
        assert _escape_like("a_b") == r"a\_b"

    def test_backslash_escaped_first(self) -> None:
        """Order matters: escaping backslash after the wildcards double-escapes them."""
        assert _escape_like("\\") == "\\\\"
        assert _escape_like("\\%") == "\\\\\\%"

    def test_plain_text_untouched(self) -> None:
        assert _escape_like("manasi") == "manasi"


class TestClampLimit:
    def test_default_when_none(self) -> None:
        assert _clamp_limit(None) == 20

    def test_ceiling(self) -> None:
        assert _clamp_limit(9999) == 100

    def test_floor(self) -> None:
        assert _clamp_limit(0) == 1
        assert _clamp_limit(-5) == 1

    def test_passthrough_in_range(self) -> None:
        assert _clamp_limit(37) == 37
