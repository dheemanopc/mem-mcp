"""Unit tests for the plugin permission registry."""

from __future__ import annotations

import pytest

from mem_mcp.auth.permissions import Permission
from mem_mcp.auth.plugin_perms import (
    list_plugin_permissions,
    register_plugin_permissions,
    reset_for_test,
    role_has_plugin_permission,
)
from mem_mcp.plugins.contract import Plugin, PluginPermission
from mem_mcp.plugins.registry import PluginRegistry


class _NewStylePlugin(Plugin):
    id = "test-plugin-a"
    api_version = "1"
    display_name = "Test A"

    def declare_permissions(self) -> list[PluginPermission | Permission]:
        return [
            PluginPermission(
                key="testa.manage_own",
                description="Manage your own test-a items",
                default_grants=frozenset({"member", "admin"}),
            ),
            PluginPermission(
                key="testa.view_admin",
                description="View admin test-a state",
                default_grants=frozenset({"admin"}),
            ),
        ]


class _LegacyEnumPlugin(Plugin):
    id = "test-plugin-b"
    api_version = "1"
    display_name = "Test B"

    def declare_permissions(self) -> list[PluginPermission | Permission]:
        return [Permission.TEAM_READ_MEMORY]  # legacy enum shape


class _CollidingPlugin(Plugin):
    id = "test-plugin-c"
    api_version = "1"
    display_name = "Test C"

    def declare_permissions(self) -> list[PluginPermission | Permission]:
        return [
            PluginPermission(
                key="testa.manage_own",  # collides with _NewStylePlugin
                description="Bad collision",
                default_grants=frozenset({"member"}),
            )
        ]


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_for_test()


class TestRegisterPluginPermissions:
    def test_default_grants_applied_to_roles(self) -> None:
        reg = PluginRegistry()
        reg.register_for_test(_NewStylePlugin())
        register_plugin_permissions(reg)

        assert role_has_plugin_permission("member", "testa.manage_own")
        assert role_has_plugin_permission("admin", "testa.manage_own")
        assert role_has_plugin_permission("admin", "testa.view_admin")
        # member was NOT granted view_admin
        assert not role_has_plugin_permission("member", "testa.view_admin")
        # unrelated role gets nothing
        assert not role_has_plugin_permission("viewer", "testa.manage_own")

    def test_legacy_enum_returns_are_skipped_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        reg = PluginRegistry()
        reg.register_for_test(_LegacyEnumPlugin())
        import logging

        with caplog.at_level(logging.WARNING):
            register_plugin_permissions(reg)
        assert any(
            "plugin_declared_core_permission_legacy_shape" in r.message for r in caplog.records
        )
        # Nothing added to the plugin grant catalog for the legacy entry
        assert list_plugin_permissions() == {}

    def test_owner_catalog_populated(self) -> None:
        reg = PluginRegistry()
        reg.register_for_test(_NewStylePlugin())
        register_plugin_permissions(reg)

        owners = list_plugin_permissions()
        assert owners["testa.manage_own"] == "test-plugin-a"
        assert owners["testa.view_admin"] == "test-plugin-a"

    def test_collision_logged_and_rejected(self, caplog: pytest.LogCaptureFixture) -> None:
        reg = PluginRegistry()
        reg.register_for_test(_NewStylePlugin())
        reg.register_for_test(_CollidingPlugin())
        import logging

        with caplog.at_level(logging.ERROR):
            register_plugin_permissions(reg)
        assert any("plugin_permission_key_collision" in r.message for r in caplog.records)
        # First registrant wins
        assert list_plugin_permissions()["testa.manage_own"] == "test-plugin-a"

    def test_idempotent_within_same_registry(self) -> None:
        reg = PluginRegistry()
        reg.register_for_test(_NewStylePlugin())
        register_plugin_permissions(reg)
        register_plugin_permissions(reg)  # second call

        # Member still has just one grant for the same key (set semantics)
        assert role_has_plugin_permission("member", "testa.manage_own")
        # Owner catalog still maps to the same plugin
        assert list_plugin_permissions()["testa.manage_own"] == "test-plugin-a"

    def test_reset_clears_state(self) -> None:
        reg = PluginRegistry()
        reg.register_for_test(_NewStylePlugin())
        register_plugin_permissions(reg)
        assert role_has_plugin_permission("member", "testa.manage_own")

        reset_for_test()
        assert not role_has_plugin_permission("member", "testa.manage_own")
        assert list_plugin_permissions() == {}
