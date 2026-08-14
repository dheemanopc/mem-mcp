"""Permission + Role enums and ROLE_PERMISSIONS matrix.

System roles live in tenant_system_roles. Team roles live in team_members.role.
A permission resolver (rbac.py) checks both.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    SYSTEM_ADMIN = "system_admin"
    SYSTEM_SUPPORT = "system_support"
    # Team roles aren't in this enum because they're free-text in team_members.role
    # (already 'admin'/'member' per enterprise PR 1.A). We treat them as strings
    # in the resolver to avoid coupling.


class Permission(StrEnum):
    # System-level
    SYSTEM_REVIEW_SIGNUPS = "system.review_signups"
    SYSTEM_APPROVE_SIGNUPS = "system.approve_signups"
    SYSTEM_REJECT_SIGNUPS = "system.reject_signups"
    SYSTEM_MANAGE_TENANTS = "system.manage_tenants"
    SYSTEM_MANAGE_ROLES = "system.manage_roles"
    SYSTEM_VIEW_AUDIT = "system.view_audit"
    SYSTEM_IMPERSONATE = "system.impersonate"  # reserved, not granted in v1

    # Team-level (resolved against team_members.role)
    TEAM_VIEW = "team.view"
    TEAM_MANAGE_MEMBERS = "team.manage_members"
    TEAM_MANAGE_SETTINGS = "team.manage_settings"
    TEAM_DELETE = "team.delete"
    TEAM_WRITE_MEMORY = "team.write_memory"
    TEAM_READ_MEMORY = "team.read_memory"


_ALL_SYSTEM = {p for p in Permission if p.value.startswith("system.")}
_ALL_TEAM = {p for p in Permission if p.value.startswith("team.")}
_SUPPORT_SYSTEM_READS = {Permission.SYSTEM_REVIEW_SIGNUPS, Permission.SYSTEM_VIEW_AUDIT}
_TEAM_ADMIN_PERMS = {
    Permission.TEAM_VIEW,
    Permission.TEAM_MANAGE_MEMBERS,
    Permission.TEAM_MANAGE_SETTINGS,
    Permission.TEAM_DELETE,
    Permission.TEAM_WRITE_MEMORY,
    Permission.TEAM_READ_MEMORY,
}
_TEAM_MEMBER_PERMS = {
    Permission.TEAM_VIEW,
    Permission.TEAM_WRITE_MEMORY,
    Permission.TEAM_READ_MEMORY,
}
_TEAM_READONLY_PERMS = {
    Permission.TEAM_VIEW,
    Permission.TEAM_READ_MEMORY,
}

# System roles: keys are Role enum values. Team roles: keys are role_key values
# from the `roles_catalog` table.
#
# Every role_key in roles_catalog MUST appear here. Spec 1 grew the catalog to
# seven roles (owner/admin/member/viewer/vendor/customer/service-bot) but this
# map was left at the legacy two, so has_permission() silently returned False
# for the other five — including `owner`, which is what memsys_create_team
# assigns to whoever creates a team. Any gate on a team permission would have
# locked out the team's own creator. `tests/unit/test_role_permissions_cover_catalog.py`
# now pins the two lists together.
ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    Role.SYSTEM_ADMIN.value: _ALL_SYSTEM,
    # ^ system_admin gets ALL system permissions
    # (design note: system_admin does NOT implicitly hold team-level write permissions —
    # that requires explicit team membership. See spec §2.)
    Role.SYSTEM_SUPPORT.value: _SUPPORT_SYSTEM_READS,
    # --- team roles (roles_catalog.role_key) ---
    # role_class 'internal'
    "owner": _TEAM_ADMIN_PERMS,
    "admin": _TEAM_ADMIN_PERMS,
    "member": _TEAM_MEMBER_PERMS,
    "viewer": _TEAM_READONLY_PERMS,
    # role_class 'external' — cross-org collaborators. Read-only by default;
    # widening these is a deliberate product decision, not a default.
    "vendor": _TEAM_READONLY_PERMS,
    "customer": _TEAM_READONLY_PERMS,
    # role_class 'service' — headless clients. May write, never administer.
    "service-bot": _TEAM_MEMBER_PERMS,
}
