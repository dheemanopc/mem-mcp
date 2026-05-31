"""Unit tests for `run_plugin_startup_hooks`.

Closes the contract/implementation mismatch documented in memsys:36ac16a1:
the SDK advertised `Plugin.on_startup(ctx)` but core never invoked it.
These tests pin the new behavior:
  - the hook is called once per plugin at lifespan startup
  - per-plugin try/except isolates one failure from killing the lifespan
  - the result map reports per-plugin success/error for observability
  - the context's per-tenant clients are stubs that raise on use
    (startup is cross-tenant — plugins must build per-tenant clients inline)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from mem_mcp.plugins.contract import Plugin, PluginContext
from mem_mcp.plugins.integration import run_plugin_startup_hooks
from mem_mcp.plugins.registry import PluginRegistry

NIL_UUID = UUID("00000000-0000-0000-0000-000000000000")


class _RecordingPlugin(Plugin):
    id = "recording"
    api_version = "1"
    display_name = "Recording"
    credential_scope = "tenant"

    def __init__(self) -> None:
        self.startup_ctx: PluginContext | None = None
        self.call_count = 0

    def register_tools(self, registry: Any) -> None:
        pass

    def register_routes(self, router: Any) -> None:
        pass

    def register_jobs(self, scheduler: Any) -> None:
        pass

    async def on_startup(self, ctx: PluginContext) -> None:
        self.call_count += 1
        self.startup_ctx = ctx


class _RaisingPlugin(Plugin):
    id = "raising"
    api_version = "1"
    display_name = "Raising"
    credential_scope = "tenant"

    def register_tools(self, registry: Any) -> None:
        pass

    def register_routes(self, router: Any) -> None:
        pass

    def register_jobs(self, scheduler: Any) -> None:
        pass

    async def on_startup(self, ctx: PluginContext) -> None:
        raise RuntimeError("intentional startup failure")


class _DefaultPlugin(Plugin):
    """Doesn't override on_startup — should hit the default no-op."""

    id = "default"
    api_version = "1"
    display_name = "Default"
    credential_scope = "tenant"

    def register_tools(self, registry: Any) -> None:
        pass

    def register_routes(self, router: Any) -> None:
        pass

    def register_jobs(self, scheduler: Any) -> None:
        pass


@pytest.mark.asyncio
async def test_on_startup_invoked_once_per_plugin() -> None:
    registry = PluginRegistry()
    rec = _RecordingPlugin()
    registry.register_for_test(rec)

    results = await run_plugin_startup_hooks(registry, pool=None)

    assert rec.call_count == 1
    assert results == {"recording": "ok"}
    assert rec.startup_ctx is not None
    assert rec.startup_ctx.plugin_id == "recording"
    assert rec.startup_ctx.tenant_id == NIL_UUID
    assert rec.startup_ctx.request_id.startswith("plugin-startup-")


@pytest.mark.asyncio
async def test_one_plugin_failure_does_not_starve_others() -> None:
    registry = PluginRegistry()
    rec = _RecordingPlugin()
    # Order matters: raise first, then verify the recorder still ran.
    registry.register_for_test(_RaisingPlugin())
    registry.register_for_test(rec)

    results = await run_plugin_startup_hooks(registry, pool=None)

    assert results["raising"].startswith("error: RuntimeError")
    assert results["recording"] == "ok"
    assert rec.call_count == 1


@pytest.mark.asyncio
async def test_default_on_startup_is_no_op() -> None:
    registry = PluginRegistry()
    registry.register_for_test(_DefaultPlugin())

    results = await run_plugin_startup_hooks(registry, pool=None)

    assert results == {"default": "ok"}


@pytest.mark.asyncio
async def test_per_tenant_clients_raise_in_startup_context() -> None:
    """Startup is cross-tenant — using per-tenant clients must fail loudly."""

    class _UsingClient(Plugin):
        id = "using-client"
        api_version = "1"
        display_name = "Using"
        credential_scope = "tenant"
        captured_error: Exception | None = None

        def register_tools(self, registry: Any) -> None:
            pass

        def register_routes(self, router: Any) -> None:
            pass

        def register_jobs(self, scheduler: Any) -> None:
            pass

        async def on_startup(self, ctx: PluginContext) -> None:
            try:
                await ctx.memories.write(content="x")  # type: ignore[call-arg]
            except NotImplementedError as exc:
                type(self).captured_error = exc
                raise

    registry = PluginRegistry()
    registry.register_for_test(_UsingClient())

    results = await run_plugin_startup_hooks(registry, pool=None)

    assert results["using-client"].startswith("error: NotImplementedError")
    assert _UsingClient.captured_error is not None
    assert "per-tenant client method 'write'" in str(_UsingClient.captured_error)
