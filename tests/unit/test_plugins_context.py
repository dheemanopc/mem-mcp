"""Unit tests for PluginContextBuilder."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from mem_mcp.plugins.context import PluginContextBuilder, _EmptyConfig
from mem_mcp.plugins.contract import Plugin
from mem_mcp.plugins.registry import PluginRegistry


class SimplePlugin(Plugin):
    """Test plugin with no config."""

    id = "simple-plugin"
    api_version = "1"
    display_name = "Simple Test Plugin"


class TestPluginContextBuilder:
    """Test PluginContext construction."""

    def test_builder_raises_for_unknown_plugin(self) -> None:
        """Test that building context for unknown plugin raises KeyError."""
        registry = PluginRegistry()
        pool = MagicMock()
        vault = MagicMock()
        audit = MagicMock()
        deps = MagicMock()
        identity_id = uuid4()

        builder = PluginContextBuilder(
            pool=pool,
            vault=vault,
            audit=audit,
            registry=registry,
            deps=deps,
            identity_id=identity_id,
        )

        tenant_id = uuid4()
        with pytest.raises(KeyError, match="not registered"):
            builder.build("nonexistent-plugin", tenant_id)

    def test_builder_uses_tenant_id_for_tenant_scope_credentials(self) -> None:
        """Test that tenant-scoped credentials use tenant_id as scope."""
        registry = PluginRegistry()
        registry.register_for_test(SimplePlugin())

        pool = MagicMock()
        vault = MagicMock()
        audit = MagicMock()
        deps = MagicMock()
        identity_id = uuid4()

        builder = PluginContextBuilder(
            pool=pool,
            vault=vault,
            audit=audit,
            registry=registry,
            deps=deps,
            identity_id=identity_id,
        )

        tenant_id = uuid4()
        ctx = builder.build("simple-plugin", tenant_id)

        assert ctx.plugin_id == "simple-plugin"
        assert ctx.tenant_id == tenant_id
        assert ctx.credentials is not None

    def test_builder_raises_for_team_scope_not_yet_supported(self) -> None:
        """Test that team-scoped credentials raise NotImplementedError."""

        class TeamScopedPlugin(Plugin):
            id = "team-scoped"
            api_version = "1"
            display_name = "Team Scoped Plugin"
            credential_scope = "team"

        registry = PluginRegistry()
        registry.register_for_test(TeamScopedPlugin())

        pool = MagicMock()
        vault = MagicMock()
        audit = MagicMock()
        deps = MagicMock()
        identity_id = uuid4()

        builder = PluginContextBuilder(
            pool=pool,
            vault=vault,
            audit=audit,
            registry=registry,
            deps=deps,
            identity_id=identity_id,
        )

        tenant_id = uuid4()
        with pytest.raises(NotImplementedError, match="not yet supported"):
            builder.build("team-scoped", tenant_id)

    def test_builder_includes_empty_config_by_default(self) -> None:
        """Test that plugins without config get _EmptyConfig."""
        registry = PluginRegistry()
        registry.register_for_test(SimplePlugin())

        pool = MagicMock()
        vault = MagicMock()
        audit = MagicMock()
        deps = MagicMock()
        identity_id = uuid4()

        builder = PluginContextBuilder(
            pool=pool,
            vault=vault,
            audit=audit,
            registry=registry,
            deps=deps,
            identity_id=identity_id,
        )

        tenant_id = uuid4()
        ctx = builder.build("simple-plugin", tenant_id)

        assert isinstance(ctx.config, _EmptyConfig)

    def test_builder_assigns_request_id_if_none_provided(self) -> None:
        """Test that a request_id is generated if not provided."""
        registry = PluginRegistry()
        registry.register_for_test(SimplePlugin())

        pool = MagicMock()
        vault = MagicMock()
        audit = MagicMock()
        deps = MagicMock()
        identity_id = uuid4()

        builder = PluginContextBuilder(
            pool=pool,
            vault=vault,
            audit=audit,
            registry=registry,
            deps=deps,
            identity_id=identity_id,
        )

        tenant_id = uuid4()
        ctx = builder.build("simple-plugin", tenant_id)

        assert ctx.request_id is not None
        assert isinstance(ctx.request_id, str)

    def test_builder_uses_provided_request_id(self) -> None:
        """Test that a provided request_id is used."""
        registry = PluginRegistry()
        registry.register_for_test(SimplePlugin())

        pool = MagicMock()
        vault = MagicMock()
        audit = MagicMock()
        deps = MagicMock()
        identity_id = uuid4()

        builder = PluginContextBuilder(
            pool=pool,
            vault=vault,
            audit=audit,
            registry=registry,
            deps=deps,
            identity_id=identity_id,
        )

        tenant_id = uuid4()
        request_id = "custom-request-id-123"
        ctx = builder.build("simple-plugin", tenant_id, request_id=request_id)

        assert ctx.request_id == request_id
