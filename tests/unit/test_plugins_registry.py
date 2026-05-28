"""Tests for mem_mcp.plugins.registry — PluginRegistry discovery + lifecycle."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from mem_mcp.plugins.contract import Plugin
from mem_mcp.plugins.registry import (
    PluginLoadError,
    PluginRegistry,
    PluginVersionError,
)


class SimplePlugin(Plugin):
    """Valid test plugin."""

    id = "simple"
    display_name = "Simple"


class NoIdPlugin(Plugin):
    """Plugin with missing id."""

    display_name = "No ID"


class BadIdPlugin(Plugin):
    """Plugin with invalid id (contains spaces)."""

    id = "bad id"
    display_name = "Bad ID"


class WrongVersionPlugin(Plugin):
    """Plugin declaring wrong api_version."""

    id = "wrong-version"
    api_version = "2"
    display_name = "Wrong Version"


class TestPluginRegistry:
    """Test PluginRegistry registration and lifecycle."""

    def test_register_and_get(self) -> None:
        """Can register a plugin and retrieve by id."""
        registry = PluginRegistry()
        plugin = SimplePlugin()
        registry.register_for_test(plugin)

        retrieved = registry.get("simple")
        assert retrieved is plugin

    def test_register_and_list_all(self) -> None:
        """all() returns all registered plugins."""
        registry = PluginRegistry()
        p1 = SimplePlugin()
        p2 = SimplePlugin()
        p2.id = "other"

        registry.register_for_test(p1)
        registry.register_for_test(p2)

        all_plugins = registry.all()
        assert len(all_plugins) == 2
        assert p1 in all_plugins
        assert p2 in all_plugins

    def test_register_duplicate_id_raises(self) -> None:
        """Registering the same id twice raises PluginLoadError."""
        registry = PluginRegistry()
        registry.register_for_test(SimplePlugin())

        with pytest.raises(PluginLoadError, match="already registered"):
            registry.register_for_test(SimplePlugin())

    def test_register_invalid_api_version_raises(self) -> None:
        """Plugin with wrong api_version raises PluginVersionError."""
        registry = PluginRegistry()

        with pytest.raises(PluginVersionError, match="api_version"):
            registry.register_for_test(WrongVersionPlugin())

    def test_register_invalid_id_empty_raises(self) -> None:
        """Plugin with empty id raises PluginLoadError."""
        registry = PluginRegistry()

        class EmptyIdPlugin(Plugin):
            id = ""
            display_name = "Empty"

        with pytest.raises(PluginLoadError, match="kebab-case"):
            registry.register_for_test(EmptyIdPlugin())

    def test_register_invalid_id_with_spaces_raises(self) -> None:
        """Plugin with spaces in id raises PluginLoadError."""
        registry = PluginRegistry()

        class SpacesIdPlugin(Plugin):
            id = "bad id"
            display_name = "Spaces"

        with pytest.raises(PluginLoadError, match="kebab-case"):
            registry.register_for_test(SpacesIdPlugin())

    def test_register_invalid_id_with_slash_raises(self) -> None:
        """Plugin with slashes in id raises PluginLoadError."""
        registry = PluginRegistry()

        class SlashIdPlugin(Plugin):
            id = "bad/id"
            display_name = "Slash"

        with pytest.raises(PluginLoadError, match="kebab-case"):
            registry.register_for_test(SlashIdPlugin())

    def test_clear_resets_state(self) -> None:
        """clear() resets the registry."""
        registry = PluginRegistry()
        registry.register_for_test(SimplePlugin())

        registry.clear()

        assert registry.get("simple") is None
        assert registry.all() == []

    def test_discover_is_idempotent(self) -> None:
        """Calling discover() twice is a no-op."""
        registry = PluginRegistry()

        # Mock entry_points to return our test plugin
        mock_ep = Mock()
        mock_ep.load.return_value = SimplePlugin
        mock_ep.name = "simple"

        with patch("mem_mcp.plugins.registry.entry_points") as mock_eps:
            mock_eps.return_value = [mock_ep]

            # First call
            registry.discover()
            assert len(registry.all()) == 1

            # Reset mock call count
            mock_ep.load.reset_mock()

            # Second call — should be no-op
            registry.discover()
            assert len(registry.all()) == 1
            mock_ep.load.assert_not_called()

    def test_discover_continues_on_bad_plugin(self) -> None:
        """If one plugin fails to load, others still load."""
        registry = PluginRegistry()

        # Create two entry points: one bad, one good
        good_ep = Mock()
        good_ep.load.return_value = SimplePlugin
        good_ep.name = "simple"

        bad_ep = Mock()
        bad_ep.load.side_effect = ValueError("Oops")
        bad_ep.name = "broken"

        with patch("mem_mcp.plugins.registry.entry_points") as mock_eps:
            mock_eps.return_value = [bad_ep, good_ep]
            registry.discover()

        # Good plugin should have loaded
        assert registry.get("simple") is not None
        assert len(registry.all()) == 1

    def test_discover_rejects_non_plugin_class(self) -> None:
        """Entry point that doesn't load a Plugin subclass is rejected."""
        registry = PluginRegistry()

        ep = Mock()
        ep.load.return_value = str  # Not a Plugin subclass
        ep.name = "bad"

        with patch("mem_mcp.plugins.registry.entry_points") as mock_eps:
            mock_eps.return_value = [ep]
            registry.discover()

        # Nothing should be registered
        assert len(registry.all()) == 0

    def test_get_returns_none_for_unknown_plugin(self) -> None:
        """get() returns None for unknown plugin id."""
        registry = PluginRegistry()
        assert registry.get("nonexistent") is None
