"""Plugin contract — what plugins implement and what they get.

Plugins implement the `Plugin` ABC. At runtime, core builds a `PluginContext`
and passes it to lifecycle hooks. The context holds Protocols (not concrete
classes) so the SDK is stable across core implementation changes.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel

if TYPE_CHECKING:
    from fastapi import APIRouter

    from mem_mcp.audit.logger import AuditLogger
    from mem_mcp.auth.permissions import Permission

# Current SDK major version. Plugins declare api_version="1"; mismatch refuses to load.
SDK_API_VERSION = "1"


# ── Plugin-facing Protocols ─────────────────────────────────────────────────


@dataclass(frozen=True)
class PluginPermission:
    """A permission declared by a plugin.

    Plugins return these from `declare_permissions()` instead of referencing
    core's `Permission` enum. The key is the wire-level string used in tool
    `required_permission`, RBAC checks, and grant lookups.

    `default_grants` names the team-role keys that should automatically hold
    this permission at startup ('admin', 'member', etc). This lets a plugin
    install without core code changes.
    """

    key: str  # e.g. "reminders.manage_own"
    description: str
    default_grants: frozenset[str] = frozenset()  # role keys, e.g. {"admin", "member"}


@runtime_checkable
class ToolRegistry(Protocol):
    """Where plugins declare their MCP tools. Names are auto-namespaced as <plugin_id>.<tool>."""

    def register(
        self,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        *,
        description: str,
        input_schema: type[BaseModel],
        required_permission: str | Permission | None = None,
    ) -> None: ...


@runtime_checkable
class JobScheduler(Protocol):
    """Where plugins declare scheduled background jobs."""

    def register(
        self,
        name: str,
        schedule: str,  # e.g. "every 60 seconds", "every 5 minutes", "@daily"
        handler: Callable[[PluginContext], Awaitable[None]],
    ) -> None: ...


@runtime_checkable
class NoticeClient(Protocol):
    """Surface async state to a user. Delivered on next MCP/web activity."""

    async def queue(
        self,
        kind: str,
        template: str,
        payload: dict[str, Any],
        *,
        ttl_seconds: int = 86400,
    ) -> None: ...


@runtime_checkable
class CredentialStore(Protocol):
    """Encrypted per-scope per-plugin credential storage. Wraps the existing SkillVault."""

    async def store(self, creds: dict[str, Any]) -> None: ...
    async def load(self) -> dict[str, Any]: ...
    async def delete(self) -> bool: ...


@runtime_checkable
class MemoryClient(Protocol):
    """Cross-plugin data plane. Plugins read/write memories via this — NOT direct DB."""

    async def write(
        self,
        content: str,
        *,
        type: str = "note",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UUID: ...

    async def search(
        self,
        query: str,
        *,
        type: str | None = None,
        tags: list[str] | None = None,
        since: Any = None,
        until: Any = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...

    async def get(self, memory_id: UUID) -> dict[str, Any] | None: ...
    async def supersede(self, memory_id: UUID, content: str) -> UUID: ...


@runtime_checkable
class PermissionResolver(Protocol):
    """RBAC check for the calling tenant. Wraps mem_mcp.auth.rbac.has_permission."""

    async def has(self, permission: str | Permission, *, team_id: UUID | None = None) -> bool: ...


# ── Plugin base class ──────────────────────────────────────────────────────


class Plugin(ABC):
    """Subclass to define a memsys skill plugin.

    Required class attributes:
        id              — globally unique kebab-case slug
        api_version     — must equal SDK_API_VERSION ("1") for v1
        display_name    — user-facing label

    Optional class attributes:
        credential_scope — "tenant" (default) or "team"; immutable after register
    """

    id: str
    api_version: str = SDK_API_VERSION
    display_name: str
    credential_scope: Literal["tenant", "team"] = "tenant"

    # ── Declaration hooks (called once at plugin load) ─────────────────────

    def get_config_schema(self) -> type[BaseModel] | None:
        """Pydantic model for the plugin's config block, or None if no config."""
        return None

    def get_migrations_path(self) -> Path | None:
        """Path to the plugin's own alembic versions directory, or None."""
        return None

    def declare_permissions(self) -> list[PluginPermission | Permission]:
        """Permissions this plugin contributes to the core RBAC matrix.

        Prefer returning `PluginPermission` instances; the `Permission` enum
        path is kept for back-compat while plugins migrate to string keys.
        """
        return []

    def register_tools(self, registry: ToolRegistry) -> None:
        """Declare MCP tools. Default: no tools."""
        return None

    def register_routes(self, router: APIRouter) -> None:
        """Declare web routes mounted under /skills/<plugin_id>/. Default: no routes."""
        return None

    def register_jobs(self, scheduler: JobScheduler) -> None:
        """Declare scheduled background jobs. Default: no jobs."""
        return None

    # ── Runtime hooks (called per-request or per-startup) ──────────────────

    async def surface_pending_state(self, tenant_id: UUID, conn: Any) -> list[str]:
        """Return strings to inject into EVERY successful tools/call response.

        Called per-request by the MCP transport. Use it for sticky state
        the user should see until they act on it (the reminders plugin
        returns pending reminders so they stay visible until snoozed /
        dismissed / resolved).

        For one-shot notices use `ctx.notices.queue(...)` instead — those
        are delivered at-most-once and don't re-surface.

        Default: empty list (plugin contributes nothing to responses).
        Expected to be a cheap query — runs on the hot path.
        """
        return []

    async def on_startup(self, ctx: PluginContext) -> None:
        """Called once after all plugins have registered. Default: no-op."""
        return None

    async def on_shutdown(self) -> None:
        """Called once at app shutdown. Default: no-op."""
        return None


# ── Context passed to plugin hooks ─────────────────────────────────────────


@dataclass(frozen=True)
class PluginContext:
    """Everything a plugin needs at runtime. Built by core, injected per request/job/tool."""

    plugin_id: str
    tenant_id: UUID
    request_id: str

    # Data + integration
    pool: Any  # asyncpg.Pool scoped to this plugin's PG schema
    memories: MemoryClient
    notices: NoticeClient
    scheduler: JobScheduler
    credentials: CredentialStore
    config: BaseModel

    # Cross-cutting
    audit: AuditLogger
    permissions: PermissionResolver
