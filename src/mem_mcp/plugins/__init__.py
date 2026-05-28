"""Public API of the memsys plugin SDK.

Plugins must subclass Plugin and register via Python entry points:

    [project.entry-points."mem_mcp.skills"]
    kite = "mem_mcp_skill_kite:Plugin"

The core scans entry points at startup, instantiates each, and calls
the lifecycle hooks. See project_plugin_contract_v1.md for the full
design.
"""

from mem_mcp.plugins.contract import (
    CredentialStore,
    JobScheduler,
    MemoryClient,
    NoticeClient,
    PermissionResolver,
    Plugin,
    PluginContext,
    ToolRegistry,
)
from mem_mcp.plugins.registry import (
    PluginLoadError,
    PluginRegistry,
    PluginVersionError,
)

__all__ = [
    "Plugin",
    "PluginContext",
    "ToolRegistry",
    "JobScheduler",
    "NoticeClient",
    "CredentialStore",
    "MemoryClient",
    "PermissionResolver",
    "PluginRegistry",
    "PluginLoadError",
    "PluginVersionError",
]
