# memsys Plugin SDK

Memsys is pluggable. This module exports the **contract** that skill plugins (like kite and reminders) consume from separate repos.

## What is a plugin?

A plugin is a Python package that subclasses `Plugin` and declares MCP tools, web routes, scheduled jobs, or permissions. At startup, memsys scans Python entry points and loads any plugins you've installed and enabled.

## Hello world

```python
from mem_mcp.plugins import Plugin

class MyPlugin(Plugin):
    id = "my-skill"
    display_name = "My Skill"

    def register_tools(self, registry):
        # Declare MCP tools here
        pass

    async def on_startup(self, ctx):
        # Called once after all plugins have registered
        pass
```

## How discovery works

Plugins register via Python entry points under the `mem_mcp.skills` group:

```toml
[project.entry-points."mem_mcp.skills"]
kite = "mem_mcp_skill_kite:Plugin"
reminders = "mem_mcp_skill_reminders:Plugin"
```

When memsys starts, `PluginRegistry.discover()` scans entry points, instantiates each plugin, and validates the API version. If there's a mismatch, the plugin is refused with a clear error.

For tests, use `registry.register_for_test(plugin_instance)` to skip entry-point scanning.

## Full design

See `/home/anand/.claude/projects/-codes-ai-work-memory-man/memory/project_plugin_contract_v1.md` for:
- Full Plugin ABC interface
- PluginContext fields (memories, notices, scheduler, credentials, audit, permissions)
- Protocol stubs for each service
- Credential scoping (tenant vs team)
- Three deployment modes (local/server/cloud)
- Roadmap and trade-offs

## What's NOT here (yet)

- Concrete MemoryClient / NoticeClient / JobScheduler implementations
- Wiring of plugin tools/routes/jobs into MCP + FastAPI (Phase 3)
- Plugin web UI / admin enable/disable panel (Phase 3)
