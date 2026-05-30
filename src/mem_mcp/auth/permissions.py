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

    # Plugin: reminders (declared here so plugin's register_tools() can reference
    # them via Permission.REMINDERS_*). Long-term these should move to a
    # plugin-declared string-keyed permission system so core isn't coupled to
    # every plugin's enum surface.
    REMINDERS_MANAGE_OWN = "reminders.manage_own"
    REMINDERS_VIEW_OWN = "reminders.view_own"


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
    Permission.REMINDERS_MANAGE_OWN,
    Permission.REMINDERS_VIEW_OWN,
}
_TEAM_MEMBER_PERMS = {
    Permission.TEAM_VIEW,
    Permission.TEAM_WRITE_MEMORY,
    Permission.TEAM_READ_MEMORY,
    Permission.REMINDERS_MANAGE_OWN,
    Permission.REMINDERS_VIEW_OWN,
}

# System roles: keys are Role enum values. Team roles: keys are 'admin'/'member'.
ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    Role.SYSTEM_ADMIN.value: _ALL_SYSTEM,
    # ^ system_admin gets ALL system permissions
    # (design note: system_admin does NOT implicitly hold team-level write permissions —
    # that requires explicit team membership. See spec §2.)
    Role.SYSTEM_SUPPORT.value: _SUPPORT_SYSTEM_READS,
    "admin": _TEAM_ADMIN_PERMS,
    "member": _TEAM_MEMBER_PERMS,
}
