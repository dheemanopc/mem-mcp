"""Matrix tests for has_permission() across all role/permission combos."""

from __future__ import annotations

from mem_mcp.auth.permissions import ROLE_PERMISSIONS, Permission, Role


def test_system_admin_has_all_system_permissions() -> None:
    """system_admin role should have all system.* permissions."""
    perms = ROLE_PERMISSIONS[Role.SYSTEM_ADMIN.value]
    for p in Permission:
        if p.value.startswith("system."):
            assert p in perms, f"system_admin missing {p}"


def test_system_admin_excludes_team_permissions() -> None:
    """system_admin does NOT implicitly hold team-level permissions."""
    perms = ROLE_PERMISSIONS[Role.SYSTEM_ADMIN.value]
    for p in Permission:
        if p.value.startswith("team."):
            assert p not in perms, f"system_admin should not have {p}"


def test_system_support_has_only_review_and_audit() -> None:
    """system_support should have only REVIEW_SIGNUPS and VIEW_AUDIT."""
    perms = ROLE_PERMISSIONS[Role.SYSTEM_SUPPORT.value]
    assert Permission.SYSTEM_REVIEW_SIGNUPS in perms
    assert Permission.SYSTEM_VIEW_AUDIT in perms
    assert Permission.SYSTEM_APPROVE_SIGNUPS not in perms
    assert Permission.SYSTEM_REJECT_SIGNUPS not in perms
    assert Permission.SYSTEM_MANAGE_TENANTS not in perms
    assert Permission.SYSTEM_MANAGE_ROLES not in perms
    assert Permission.SYSTEM_IMPERSONATE not in perms


def test_team_admin_has_all_team_perms() -> None:
    """team admin should have all team.* permissions."""
    perms = ROLE_PERMISSIONS["admin"]
    assert Permission.TEAM_VIEW in perms
    assert Permission.TEAM_MANAGE_MEMBERS in perms
    assert Permission.TEAM_MANAGE_SETTINGS in perms
    assert Permission.TEAM_DELETE in perms
    assert Permission.TEAM_WRITE_MEMORY in perms
    assert Permission.TEAM_READ_MEMORY in perms


def test_team_admin_excludes_system_perms() -> None:
    """team admin should not have system.* permissions."""
    perms = ROLE_PERMISSIONS["admin"]
    for p in Permission:
        if p.value.startswith("system."):
            assert p not in perms, f"team admin should not have {p}"


def test_team_member_has_limited_perms() -> None:
    """team member should have read + write but not manage/delete."""
    perms = ROLE_PERMISSIONS["member"]
    assert Permission.TEAM_VIEW in perms
    assert Permission.TEAM_WRITE_MEMORY in perms
    assert Permission.TEAM_READ_MEMORY in perms
    assert Permission.TEAM_MANAGE_MEMBERS not in perms
    assert Permission.TEAM_MANAGE_SETTINGS not in perms
    assert Permission.TEAM_DELETE not in perms


def test_team_member_excludes_system_perms() -> None:
    """team member should not have system.* permissions."""
    perms = ROLE_PERMISSIONS["member"]
    for p in Permission:
        if p.value.startswith("system."):
            assert p not in perms, f"team member should not have {p}"
