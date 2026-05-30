"""Wire plugins into the live FastAPI app + MCP server at lifespan startup."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from mem_mcp.auth.permissions import Permission
from mem_mcp.plugins.registry import PluginRegistry

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegisteredTool:
    plugin_id: str
    namespaced_name: str  # e.g. "kite.ta_session_open"
    description: str
    input_schema: type[BaseModel]
    handler: Callable[..., Awaitable[Any]]
    required_permission: str | None  # string permission key, e.g. "team.read_memory"


class ToolRegistryImpl:
    """Where plugins declare MCP tools. Namespaces every tool with the plugin id."""

    def __init__(self, plugin_id: str) -> None:
        self._plugin_id = plugin_id
        self._tools: list[RegisteredTool] = []

    def register(
        self,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        *,
        description: str,
        input_schema: type[BaseModel],
        required_permission: str | Permission | None = None,
    ) -> None:
        # Tool name is plugin_id.name; plugin cannot register in another namespace
        namespaced = f"{self._plugin_id}.{name}"
        # Normalize Permission enum → string. Plugins should pass strings; the
        # enum path is kept transitional while plugins migrate.
        perm_str: str | None = (
            required_permission.value
            if isinstance(required_permission, Permission)
            else required_permission
        )
        self._tools.append(
            RegisteredTool(
                plugin_id=self._plugin_id,
                namespaced_name=namespaced,
                description=description,
                input_schema=input_schema,
                handler=handler,
                required_permission=perm_str,
            )
        )
        log.info("plugin_tool_registered", extra={"plugin_id": self._plugin_id, "tool": namespaced})

    def list_tools(self) -> list[RegisteredTool]:
        return list(self._tools)


def mount_plugin_routes(app: Any, registry: PluginRegistry) -> None:
    """For each plugin, call register_routes() and mount the result under /skills/<id>."""
    for plugin in registry.all():
        sub_router = APIRouter()
        try:
            plugin.register_routes(sub_router)
        except Exception:
            log.exception("plugin_routes_register_failed", extra={"plugin_id": plugin.id})
            continue
        # Only mount if the plugin actually added routes
        if sub_router.routes:
            app.include_router(sub_router, prefix=f"/skills/{plugin.id}")
            log.info(
                "plugin_routes_mounted",
                extra={
                    "plugin_id": plugin.id,
                    "prefix": f"/skills/{plugin.id}",
                    "route_count": len(sub_router.routes),
                },
            )


def collect_plugin_tools(registry: PluginRegistry) -> list[RegisteredTool]:
    """For each plugin, call register_tools() and return all declared tools.

    Returned list can be fed to the MCP server's tool registration. Wiring the
    MCP server to actually serve these tools is out of scope of THIS PR — the
    return value just needs to be queryable so the next PR can hook them in.
    """
    all_tools: list[RegisteredTool] = []
    for plugin in registry.all():
        tool_reg = ToolRegistryImpl(plugin.id)
        try:
            plugin.register_tools(tool_reg)
        except Exception:
            log.exception("plugin_tools_register_failed", extra={"plugin_id": plugin.id})
            continue
        all_tools.extend(tool_reg.list_tools())
    return all_tools


async def run_plugin_jobs_setup(registry: PluginRegistry) -> dict[str, list[Any]]:
    """For each plugin, call register_jobs() and return what they declared.

    Actual firing is TODO (future PR with APScheduler or systemd unit gen).
    """
    from mem_mcp.plugins.clients.jobs import JobSchedulerImpl

    result: dict[str, list[Any]] = {}
    for plugin in registry.all():
        sched = JobSchedulerImpl(plugin.id)
        try:
            plugin.register_jobs(sched)
        except Exception:
            log.exception("plugin_jobs_register_failed", extra={"plugin_id": plugin.id})
            continue
        result[plugin.id] = sched.list_jobs()
    return result
