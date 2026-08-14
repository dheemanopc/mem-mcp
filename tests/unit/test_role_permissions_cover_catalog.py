"""ROLE_PERMISSIONS must cover every role_key the RBAC catalog can hand out.

This is the drift guard for the bug that made the team-tool authz fix
non-trivial: Spec 1 grew `roles_catalog` to seven roles, `ROLE_PERMISSIONS`
stayed at the legacy two, and `has_permission()` therefore returned False for
`owner` — the role `memsys_create_team` gives a team's own creator. Gating
anything on a team permission would have locked out the person who made the
team, which is exactly the failure mode the web handlers already shipped.

DB-free on purpose so it runs in the `unit` CI job. The catalog role_keys are
mirrored from the migration; if that list changes, this test fails and forces
ROLE_PERMISSIONS to be updated with it.
"""

from __future__ import annotations

import pytest

from mem_mcp.auth.permissions import ROLE_PERMISSIONS, Permission, Role

# Mirrors roles_catalog seed. Kept as a literal so a catalog change breaks this
# test rather than silently agreeing with itself.
CATALOG_ROLE_KEYS = frozenset(
    {"owner", "admin", "member", "viewer", "vendor", "customer", "service-bot"}
)

SYSTEM_ROLE_KEYS = frozenset({Role.SYSTEM_ADMIN.value, Role.SYSTEM_SUPPORT.value})


def test_every_catalog_role_has_an_entry() -> None:
    missing = CATALOG_ROLE_KEYS - ROLE_PERMISSIONS.keys()
    assert not missing, (
        f"roles_catalog role_keys with no ROLE_PERMISSIONS entry: {sorted(missing)}. "
        "has_permission() returns False for these, silently."
    )


def test_no_stray_entries() -> None:
    """Every key is either a system role or a catalog role — nothing invented."""
    stray = ROLE_PERMISSIONS.keys() - CATALOG_ROLE_KEYS - SYSTEM_ROLE_KEYS
    assert not stray, f"ROLE_PERMISSIONS keys matching no known role: {sorted(stray)}"


def test_owner_can_manage_members() -> None:
    """The regression this whole guard exists for."""
    assert Permission.TEAM_MANAGE_MEMBERS in ROLE_PERMISSIONS["owner"]


@pytest.mark.parametrize("role_key", ["member", "viewer", "vendor", "customer", "service-bot"])
def test_non_admin_roles_cannot_manage_members(role_key: str) -> None:
    assert Permission.TEAM_MANAGE_MEMBERS not in ROLE_PERMISSIONS[role_key]
    assert Permission.TEAM_DELETE not in ROLE_PERMISSIONS[role_key]


@pytest.mark.parametrize("role_key", ["vendor", "customer"])
def test_external_roles_are_read_only(role_key: str) -> None:
    """External collaborators must not write memories by default."""
    assert Permission.TEAM_WRITE_MEMORY not in ROLE_PERMISSIONS[role_key]


def test_team_roles_hold_no_system_permissions() -> None:
    for role_key in CATALOG_ROLE_KEYS:
        assert not any(
            p.value.startswith("system.") for p in ROLE_PERMISSIONS[role_key]
        ), f"team role {role_key} carries a system.* permission"
