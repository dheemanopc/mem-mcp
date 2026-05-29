"""Reminders plugin — state-machine-driven reminder system."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from mem_mcp.auth.permissions import Permission
from mem_mcp.db import get_pool
from mem_mcp.plugins.contract import Plugin
from mem_mcp.skills.reminders import job, tools

if TYPE_CHECKING:
    from mem_mcp.plugins.contract import JobScheduler, ToolRegistry


class RemindersPlugin(Plugin):
    """Reminders plugin — deterministic firing, state machine, no LLM."""

    id = "reminders"
    api_version = "1"
    display_name = "Reminders"
    credential_scope = "tenant"

    def declare_permissions(self) -> list[Permission]:
        """Declare permissions for reminders."""
        return [
            Permission.REMINDERS_MANAGE_OWN,
            Permission.REMINDERS_VIEW_OWN,
        ]

    def register_tools(self, registry: ToolRegistry) -> None:
        """Register the 6 MCP tools."""

        def make_handler(actual_handler: Any) -> Any:
            """Wrap a tool handler to inject the pool."""

            async def handler(inp: Any, tenant_id: UUID, request_id: str) -> Any:
                pool = get_pool()
                return await actual_handler(inp, tenant_id, pool)

            return handler

        registry.register(
            name="create_reminder",
            handler=make_handler(tools.create_reminder_handler),
            description="Create a reminder with optional fire_at time",
            input_schema=tools.CreateReminderInput,
            required_permission=Permission.REMINDERS_MANAGE_OWN,
        )
        registry.register(
            name="snooze",
            handler=make_handler(tools.snooze_handler),
            description="Snooze a reminder until a specified time",
            input_schema=tools.SnoozeInput,
            required_permission=Permission.REMINDERS_MANAGE_OWN,
        )
        registry.register(
            name="dismiss",
            handler=make_handler(tools.dismiss_handler),
            description="Dismiss a reminder (silence but don't resolve)",
            input_schema=tools.DismissInput,
            required_permission=Permission.REMINDERS_MANAGE_OWN,
        )
        registry.register(
            name="resolve",
            handler=make_handler(tools.resolve_handler),
            description="Resolve (complete) a reminder",
            input_schema=tools.ResolveInput,
            required_permission=Permission.REMINDERS_MANAGE_OWN,
        )
        registry.register(
            name="list_reminders",
            handler=make_handler(tools.list_reminders_handler),
            description="List reminders, optionally filtered by state",
            input_schema=tools.ListRemindersInput,
            required_permission=Permission.REMINDERS_VIEW_OWN,
        )
        registry.register(
            name="get_reminder",
            handler=make_handler(tools.get_reminder_handler),
            description="Get a single reminder by ID",
            input_schema=tools.GetReminderInput,
            required_permission=Permission.REMINDERS_VIEW_OWN,
        )

    def register_jobs(self, scheduler: JobScheduler) -> None:
        """Register background jobs.

        NOTE: The current JobScheduler is a stub (PR #261) — it stores jobs but
        does not actually fire them. Real firing infra will come in a future PR.
        """
        scheduler.register(
            name="fire_due_reminders",
            schedule="every 60 seconds",
            handler=job.fire_due_reminders_job,
        )
