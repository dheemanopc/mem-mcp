"""Unit tests for reminders plugin metadata and registration."""

from mem_mcp.auth.permissions import Permission
from mem_mcp.plugins.contract import Plugin
from mem_mcp.plugins.integration import ToolRegistryImpl
from mem_mcp.skills.reminders.plugin import RemindersPlugin


class TestRemindersPlugin:
    """Test plugin metadata and registration."""

    def test_plugin_id_and_version(self) -> None:
        """Plugin has correct id and api_version."""
        plugin = RemindersPlugin()
        assert plugin.id == "reminders"
        assert plugin.api_version == "1"
        assert plugin.display_name == "Reminders"
        assert plugin.credential_scope == "tenant"

    def test_plugin_is_subclass_of_plugin(self) -> None:
        """RemindersPlugin implements Plugin ABC."""
        plugin = RemindersPlugin()
        assert isinstance(plugin, Plugin)

    def test_plugin_declares_permissions(self) -> None:
        """Plugin declares reminders permissions."""
        plugin = RemindersPlugin()
        perms = plugin.declare_permissions()
        assert Permission.REMINDERS_MANAGE_OWN in perms
        assert Permission.REMINDERS_VIEW_OWN in perms

    def test_plugin_registers_6_tools(self) -> None:
        """Plugin registers all 6 tools."""
        plugin = RemindersPlugin()
        registry = ToolRegistryImpl(plugin.id)
        plugin.register_tools(registry)
        tools = registry.list_tools()
        assert len(tools) == 6
        names = {t.namespaced_name for t in tools}
        expected = {
            "reminders.create_reminder",
            "reminders.snooze",
            "reminders.dismiss",
            "reminders.resolve",
            "reminders.list_reminders",
            "reminders.get_reminder",
        }
        assert names == expected

    def test_tool_namespacing(self) -> None:
        """Tools are properly namespaced with plugin id."""
        plugin = RemindersPlugin()
        registry = ToolRegistryImpl(plugin.id)
        plugin.register_tools(registry)
        tools = registry.list_tools()
        for tool in tools:
            assert tool.namespaced_name.startswith("reminders.")

    def test_plugin_registers_fire_due_reminders_job(self) -> None:
        """Plugin registers the fire_due_reminders job."""
        plugin = RemindersPlugin()

        # JobScheduler is a Protocol, so we'll mock it
        class MockJobScheduler:
            def __init__(self) -> None:
                self.registered_jobs: list[tuple[str, str, object]] = []

            def register(self, name: str, schedule: str, handler: object) -> None:
                self.registered_jobs.append((name, schedule, handler))

        scheduler = MockJobScheduler()  # type: ignore[assignment]
        plugin.register_jobs(scheduler)  # type: ignore[arg-type]

        assert len(scheduler.registered_jobs) == 1
        assert scheduler.registered_jobs[0][0] == "fire_due_reminders"
        assert scheduler.registered_jobs[0][1] == "every 60 seconds"
