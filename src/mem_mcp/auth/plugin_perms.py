"""Runtime registry of plugin-declared permissions + their role grants.

Goal: plugins declare permissions as string keys plus a set of role names that
should hold each permission by default. Core's `_TEAM_MEMBER_PERMS` etc. stay
as the canonical core-perm sets; this module holds the parallel set of plugin
perms keyed by role.

The runtime `has_permission` lookup in `rbac.py` consults both — first core's
`ROLE_PERMISSIONS`, then this catalog. That keeps plugins from having to edit
core every time they declare a new permission.

Built at app lifespan startup via `register_plugin_permissions(plugin_registry)`.
Idempotent: calling twice with the same plugins is a no-op.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mem_mcp.auth.permissions import Permission
from mem_mcp.plugins.contract import PluginPermission

if TYPE_CHECKING:
    from mem_mcp.plugins.registry import PluginRegistry

log = logging.getLogger(__name__)


# Role name -> set of permission keys (strings) granted by plugins.
# E.g. {"member": {"reminders.manage_own", "reminders.view_own"}, ...}
_PLUGIN_ROLE_GRANTS: dict[str, set[str]] = {}

# Inverse: permission key -> declaring plugin id (for diagnostics + future
# admin-UI listing). E.g. {"reminders.manage_own": "reminders"}
_PLUGIN_PERMISSION_OWNERS: dict[str, str] = {}


def reset_for_test() -> None:
    """Clear both registries — call in tests that rebuild the plugin registry."""
    _PLUGIN_ROLE_GRANTS.clear()
    _PLUGIN_PERMISSION_OWNERS.clear()


def register_plugin_permissions(plugin_registry: PluginRegistry) -> None:
    """Walk each plugin's declare_permissions() and populate the runtime registries.

    Accepts both new-shape `PluginPermission` and legacy `Permission` enum
    returns. Legacy returns are no-ops here — they're assumed to already be
    granted via `ROLE_PERMISSIONS` in core. Plugins should migrate to
    `PluginPermission` for proper namespaced declaration with default grants.
    """
    for plugin in plugin_registry.all():
        try:
            declared = plugin.declare_permissions()
        except Exception:
            log.exception("plugin_declare_permissions_failed", extra={"plugin_id": plugin.id})
            continue

        for decl in declared:
            if isinstance(decl, Permission):
                # Legacy: enum value, already in core. Skip — core grants handle it.
                log.warning(
                    "plugin_declared_core_permission_legacy_shape",
                    extra={
                        "plugin_id": plugin.id,
                        "permission": decl.value,
                        "guidance": "switch to PluginPermission(key=..., default_grants=...)",
                    },
                )
                continue
            # By this point decl is narrowed to PluginPermission (Permission was
            # handled above). A defensive isinstance check would be dead code
            # per mypy; plugins returning wrong types will fail at the attribute
            # access below.
            key = decl.key
            existing_owner = _PLUGIN_PERMISSION_OWNERS.get(key)
            if existing_owner and existing_owner != plugin.id:
                log.error(
                    "plugin_permission_key_collision",
                    extra={
                        "key": key,
                        "existing_plugin": existing_owner,
                        "new_plugin": plugin.id,
                    },
                )
                continue
            _PLUGIN_PERMISSION_OWNERS[key] = plugin.id

            for role in decl.default_grants:
                _PLUGIN_ROLE_GRANTS.setdefault(role, set()).add(key)

        log.info(
            "plugin_permissions_registered",
            extra={
                "plugin_id": plugin.id,
                "declared_count": len(declared),
            },
        )


def role_has_plugin_permission(role: str, permission_key: str) -> bool:
    """True if `role` was granted `permission_key` by any plugin's declare_permissions()."""
    return permission_key in _PLUGIN_ROLE_GRANTS.get(role, set())


def list_plugin_permissions() -> dict[str, str]:
    """Diagnostic: return {permission_key: declaring_plugin_id}. Used by admin tooling."""
    return dict(_PLUGIN_PERMISSION_OWNERS)
