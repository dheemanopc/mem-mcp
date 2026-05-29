"""Unit tests for KitePlugin."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from mem_mcp.plugins.integration import ToolRegistryImpl
from mem_mcp.skills.kite.plugin import KitePlugin


class TestKitePlugin:
    """Test KitePlugin basic properties."""

    def test_plugin_has_correct_id(self) -> None:
        """Test that plugin has correct id."""
        plugin = KitePlugin()
        assert plugin.id == "kite"

    def test_plugin_has_correct_display_name(self) -> None:
        """Test that plugin has correct display name."""
        plugin = KitePlugin()
        assert plugin.display_name == "Zerodha Kite"

    def test_plugin_has_correct_api_version(self) -> None:
        """Test that plugin declares api_version 1."""
        plugin = KitePlugin()
        assert plugin.api_version == "1"

    def test_plugin_credential_scope_is_tenant(self) -> None:
        """Test that credential scope is tenant (not team)."""
        plugin = KitePlugin()
        assert plugin.credential_scope == "tenant"

    @patch("mem_mcp.skills.kite.plugin.SkillVault.from_settings")
    def test_register_tools_when_vault_disabled(self, mock_vault_from_settings: Any) -> None:
        """Test register_tools gracefully handles vault disabled."""
        from mem_mcp.skills.vault import SkillVaultDisabledError

        mock_vault_from_settings.side_effect = SkillVaultDisabledError("disabled")
        plugin = KitePlugin()
        registry = ToolRegistryImpl("kite")

        # Should not raise; should log warning and return early
        plugin.register_tools(registry)

        # No tools should be registered
        assert len(registry.list_tools()) == 0

    @patch("mem_mcp.skills.kite.plugin.SkillVault.from_settings")
    def test_register_tools_when_vault_fails(self, mock_vault_from_settings: Any) -> None:
        """Test register_tools gracefully handles vault init errors."""
        mock_vault_from_settings.side_effect = RuntimeError("unexpected error")
        plugin = KitePlugin()
        registry = ToolRegistryImpl("kite")

        # Should not raise; should log exception and return early
        plugin.register_tools(registry)

        # No tools should be registered
        assert len(registry.list_tools()) == 0

    @patch("mem_mcp.config.get_settings")
    def test_register_tools_declares_14_tools(self, mock_get_settings: Any) -> None:
        """Test that plugin registers exactly 14 kite tools."""
        from mem_mcp.skills.vault import SkillVault

        # Mock settings and vault
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        mock_vault = MagicMock(spec=SkillVault)
        with patch("mem_mcp.skills.kite.plugin.SkillVault.from_settings", return_value=mock_vault):
            plugin = KitePlugin()
            registry = ToolRegistryImpl("kite")
            plugin.register_tools(registry)

        # Check that we have 14 tools
        tools = registry.list_tools()
        assert len(tools) == 14

    @patch("mem_mcp.config.get_settings")
    def test_kite_tools_are_namespaced(self, mock_get_settings: Any) -> None:
        """Test that kite tools are namespaced as kite.* not just names."""
        from mem_mcp.skills.vault import SkillVault

        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        mock_vault = MagicMock(spec=SkillVault)
        with patch("mem_mcp.skills.kite.plugin.SkillVault.from_settings", return_value=mock_vault):
            plugin = KitePlugin()
            registry = ToolRegistryImpl("kite")
            plugin.register_tools(registry)

        tools = registry.list_tools()
        # All tools should start with "kite."
        for tool in tools:
            assert tool.namespaced_name.startswith(
                "kite."
            ), f"tool {tool.namespaced_name} not properly namespaced"

    @patch("mem_mcp.config.get_settings")
    def test_kite_tools_have_descriptions(self, mock_get_settings: Any) -> None:
        """Test that kite tools have descriptions."""
        from mem_mcp.skills.vault import SkillVault

        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        mock_vault = MagicMock(spec=SkillVault)
        with patch("mem_mcp.skills.kite.plugin.SkillVault.from_settings", return_value=mock_vault):
            plugin = KitePlugin()
            registry = ToolRegistryImpl("kite")
            plugin.register_tools(registry)

        tools = registry.list_tools()
        for tool in tools:
            assert tool.description, f"tool {tool.namespaced_name} has no description"
            assert len(tool.description) > 0

    @patch("mem_mcp.config.get_settings")
    def test_get_holdings_handler_signature(self, mock_get_settings: Any) -> None:
        """Test that handlers have correct signature for calling."""
        from mem_mcp.skills.vault import SkillVault

        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        mock_vault = MagicMock(spec=SkillVault)
        with patch("mem_mcp.skills.kite.plugin.SkillVault.from_settings", return_value=mock_vault):
            plugin = KitePlugin()
            registry = ToolRegistryImpl("kite")
            plugin.register_tools(registry)

        tools = registry.list_tools()
        get_holdings = next(t for t in tools if t.namespaced_name == "kite.get_holdings")

        # Verify it has a handler
        assert get_holdings.handler is not None
        # Verify handler is awaitable
        import inspect

        assert inspect.iscoroutinefunction(get_holdings.handler)
