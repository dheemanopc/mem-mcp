"""Unit tests for plugin integration (tool/route/job registration)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute
from pydantic import BaseModel

from mem_mcp.plugins.contract import Plugin
from mem_mcp.plugins.contract import ToolRegistry as ToolRegistryProtocol
from mem_mcp.plugins.integration import (
    ToolRegistryImpl,
    collect_plugin_tools,
    mount_plugin_routes,
    run_plugin_jobs_setup,
)
from mem_mcp.plugins.registry import PluginRegistry


class FakeInput(BaseModel):
    """Fake input model for test tool."""

    message: str


class FakePlugin(Plugin):
    """Minimal test plugin that declares one tool, one route, one job."""

    id = "fake-test-skill"
    api_version = "1"
    display_name = "Fake Test Skill"

    def register_tools(self, registry: ToolRegistryProtocol) -> None:
        """Register one tool."""

        async def handle_tool(ctx: Any, inp: FakeInput) -> Any:
            return {"result": f"handled: {inp.message}"}

        registry.register(
            "echo",
            handle_tool,
            description="Echo back the message",
            input_schema=FakeInput,
        )

    def register_routes(self, router: APIRouter) -> None:
        """Register one route."""

        @router.get("/test")
        async def test_route() -> dict[str, str]:
            return {"status": "ok"}

    def register_jobs(self, scheduler: Any) -> None:
        """Register one job."""

        async def dummy_job(ctx: Any) -> None:
            pass

        scheduler.register(
            "test_job",
            "every 60 seconds",
            dummy_job,
        )


class TestToolRegistryImpl:
    """Test ToolRegistry impl for namespace enforcement."""

    def test_tool_namespaced_with_plugin_id(self) -> None:
        """Test that tools are namespaced as plugin_id.tool_name."""
        registry = ToolRegistryImpl("kite")

        async def dummy_handler(ctx: Any, inp: FakeInput) -> Any:
            pass

        registry.register(
            "ta_session_open",
            dummy_handler,
            description="Open a TA session",
            input_schema=FakeInput,
        )

        tools = registry.list_tools()
        assert len(tools) == 1
        assert tools[0].namespaced_name == "kite_ta_session_open"

    def test_multiple_tools_all_namespaced(self) -> None:
        """Test that multiple tools are all namespaced correctly."""
        registry = ToolRegistryImpl("reminders")

        async def dummy_handler(ctx: Any, inp: FakeInput) -> Any:
            pass

        for name in ["create", "list", "update"]:
            registry.register(
                name,
                dummy_handler,
                description=f"Test {name}",
                input_schema=FakeInput,
            )

        tools = registry.list_tools()
        assert len(tools) == 3
        assert all(t.namespaced_name.startswith("reminders_") for t in tools)


class TestCollectPluginTools:
    """Test tool collection from registered plugins."""

    def test_collect_tools_from_plugin(self) -> None:
        """Test that collect_plugin_tools gathers declared tools."""
        registry = PluginRegistry()
        registry.register_for_test(FakePlugin())

        tools = collect_plugin_tools(registry)
        assert len(tools) == 1
        assert tools[0].plugin_id == "fake-test-skill"
        assert tools[0].namespaced_name == "fake-test-skill_echo"
        assert tools[0].description == "Echo back the message"

    def test_collect_handles_plugin_registration_error(self) -> None:
        """Test that tool collection continues if a plugin fails."""

        class BadPlugin(Plugin):
            id = "bad-plugin"
            api_version = "1"
            display_name = "Bad Plugin"

            def register_tools(self, registry: ToolRegistryProtocol) -> None:
                raise RuntimeError("intentional failure")

        registry = PluginRegistry()
        registry.register_for_test(FakePlugin())
        registry.register_for_test(BadPlugin())

        # Should not raise; bad plugin skipped, good plugin collected
        tools = collect_plugin_tools(registry)
        assert len(tools) == 1  # Only fake-test-skill


class TestMountPluginRoutes:
    """Test FastAPI route mounting."""

    def test_mount_routes_from_plugin(self) -> None:
        """Test that routes are mounted under /skills/<plugin_id>/."""
        from fastapi import FastAPI

        app = FastAPI()
        registry = PluginRegistry()
        registry.register_for_test(FakePlugin())

        mount_plugin_routes(app, registry)

        # Check that routes were added to the app
        # The route should be at /skills/fake-test-skill/test
        route_paths = [str(r.path) for r in app.routes if isinstance(r, APIRoute)]
        assert any("/skills/fake-test-skill" in p for p in route_paths)

    def test_mount_handles_plugin_registration_error(self) -> None:
        """Test that mounting continues if a plugin fails."""
        from fastapi import FastAPI

        class BadPlugin(Plugin):
            id = "bad-plugin"
            api_version = "1"
            display_name = "Bad Plugin"

            def register_routes(self, router: APIRouter) -> None:
                raise RuntimeError("intentional failure")

        app = FastAPI()
        registry = PluginRegistry()
        registry.register_for_test(FakePlugin())
        registry.register_for_test(BadPlugin())

        # Should not raise; bad plugin skipped
        mount_plugin_routes(app, registry)

        route_paths = [str(r.path) for r in app.routes if isinstance(r, APIRoute)]
        assert any("/skills/fake-test-skill" in p for p in route_paths)


class TestRunPluginJobsSetup:
    """Test job registration collection."""

    @pytest.mark.asyncio
    async def test_collect_jobs_from_plugin(self) -> None:
        """Test that job declarations are collected."""
        registry = PluginRegistry()
        registry.register_for_test(FakePlugin())

        result = await run_plugin_jobs_setup(registry)
        assert "fake-test-skill" in result
        jobs = result["fake-test-skill"]
        assert len(jobs) == 1
        assert jobs[0].name == "test_job"
        assert jobs[0].schedule == "every 60 seconds"

    @pytest.mark.asyncio
    async def test_collect_handles_plugin_registration_error(self) -> None:
        """Test that job collection continues if a plugin fails."""

        class BadPlugin(Plugin):
            id = "bad-plugin"
            api_version = "1"
            display_name = "Bad Plugin"

            def register_jobs(self, scheduler: Any) -> None:
                raise RuntimeError("intentional failure")

        registry = PluginRegistry()
        registry.register_for_test(FakePlugin())
        registry.register_for_test(BadPlugin())

        # Should not raise; bad plugin skipped
        result = await run_plugin_jobs_setup(registry)
        assert "fake-test-skill" in result
        assert "bad-plugin" not in result or result["bad-plugin"] == []
