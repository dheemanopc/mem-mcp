"""Tests for mem_mcp.plugins.contract — Plugin ABC + PluginContext."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import BaseModel

from mem_mcp.plugins.contract import (
    SDK_API_VERSION,
    CredentialStore,
    JobScheduler,
    MemoryClient,
    NoticeClient,
    PermissionResolver,
    Plugin,
    PluginContext,
    ToolRegistry,
)


class TestPluginSubclass:
    """Test Plugin ABC subclass requirements."""

    def test_plugin_requires_id(self) -> None:
        """Subclass must set id."""

        class BadPlugin(Plugin):
            display_name = "Bad"

        with pytest.raises(AttributeError):
            _ = BadPlugin().id

    def test_plugin_requires_display_name(self) -> None:
        """Subclass must set display_name."""

        class BadPlugin(Plugin):
            id = "bad"

        with pytest.raises(AttributeError):
            _ = BadPlugin().display_name

    def test_plugin_has_default_api_version(self) -> None:
        """api_version defaults to SDK_API_VERSION."""

        class GoodPlugin(Plugin):
            id = "good"
            display_name = "Good"

        assert GoodPlugin().api_version == SDK_API_VERSION

    def test_credential_scope_defaults_to_tenant(self) -> None:
        """credential_scope defaults to 'tenant'."""

        class GoodPlugin(Plugin):
            id = "good"
            display_name = "Good"

        assert GoodPlugin().credential_scope == "tenant"

    def test_credential_scope_can_be_team(self) -> None:
        """credential_scope can be set to 'team'."""

        class TeamPlugin(Plugin):
            id = "team"
            display_name = "Team"
            credential_scope = "team"

        assert TeamPlugin().credential_scope == "team"


class TestPluginContextFrozen:
    """Test that PluginContext is immutable."""

    def test_plugin_context_is_frozen(self) -> None:
        """PluginContext fields cannot be mutated."""

        class DummyConfig(BaseModel):
            pass

        ctx = PluginContext(
            plugin_id="test",
            tenant_id=uuid4(),
            request_id="req-123",
            pool=None,
            memories=None,  # type: ignore[arg-type]
            notices=None,  # type: ignore[arg-type]
            scheduler=None,  # type: ignore[arg-type]
            credentials=None,  # type: ignore[arg-type]
            config=DummyConfig(),
            audit=None,  # type: ignore[arg-type]
            permissions=None,  # type: ignore[arg-type]
        )

        with pytest.raises(AttributeError):
            ctx.plugin_id = "changed"  # type: ignore[misc]


class TestProtocolsRuntimeCheckable:
    """Test that Protocols are runtime_checkable."""

    class StubToolRegistry:
        def register(self, name, handler, *, description, input_schema, required_permission=None):
            pass

    class StubJobScheduler:
        def register(self, name, schedule, handler):
            pass

    class StubNoticeClient:
        async def queue(self, kind, template, payload, *, ttl_seconds=86400):
            pass

    class StubCredentialStore:
        async def store(self, creds):
            pass

        async def load(self):
            pass

        async def delete(self):
            pass

    class StubMemoryClient:
        async def write(self, content, *, type="note", tags=None, metadata=None):
            pass

        async def search(self, query, *, type=None, tags=None, since=None, until=None, limit=20):
            pass

        async def get(self, memory_id):
            pass

        async def supersede(self, memory_id, content):
            pass

    class StubPermissionResolver:
        async def has(self, permission, *, team_id=None):
            pass

    def test_tool_registry_protocol_check(self) -> None:
        """isinstance() works for ToolRegistry."""
        stub = self.StubToolRegistry()
        assert isinstance(stub, ToolRegistry)

    def test_job_scheduler_protocol_check(self) -> None:
        """isinstance() works for JobScheduler."""
        stub = self.StubJobScheduler()
        assert isinstance(stub, JobScheduler)

    def test_notice_client_protocol_check(self) -> None:
        """isinstance() works for NoticeClient."""
        stub = self.StubNoticeClient()
        assert isinstance(stub, NoticeClient)

    def test_credential_store_protocol_check(self) -> None:
        """isinstance() works for CredentialStore."""
        stub = self.StubCredentialStore()
        assert isinstance(stub, CredentialStore)

    def test_memory_client_protocol_check(self) -> None:
        """isinstance() works for MemoryClient."""
        stub = self.StubMemoryClient()
        assert isinstance(stub, MemoryClient)

    def test_permission_resolver_protocol_check(self) -> None:
        """isinstance() works for PermissionResolver."""
        stub = self.StubPermissionResolver()
        assert isinstance(stub, PermissionResolver)
