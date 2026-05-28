"""PluginContext builder — wires concrete clients into a context per request/job."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
from pydantic import BaseModel

from mem_mcp.audit.logger import AuditLogger
from mem_mcp.plugins.clients.credentials import CredentialStoreImpl
from mem_mcp.plugins.clients.jobs import JobSchedulerImpl
from mem_mcp.plugins.clients.memory import MemoryClientImpl
from mem_mcp.plugins.clients.notices import NoticeClientImpl
from mem_mcp.plugins.clients.permissions import PermissionResolverImpl
from mem_mcp.plugins.contract import (
    CredentialStore,
    JobScheduler,
    MemoryClient,
    NoticeClient,
    PermissionResolver,
    PluginContext,
)
from mem_mcp.skills.vault import SkillVault

if TYPE_CHECKING:
    from mem_mcp.mcp.tools._deps import ToolDeps
    from mem_mcp.plugins.registry import PluginRegistry


class PluginContextBuilder:
    """Build a PluginContext for a specific (plugin, tenant, request)."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        vault: SkillVault,
        audit: AuditLogger,
        registry: PluginRegistry,
        deps: ToolDeps,
        identity_id: UUID,
    ) -> None:
        self._pool = pool
        self._vault = vault
        self._audit = audit
        self._registry = registry
        self._deps = deps
        self._identity_id = identity_id

    def build(
        self,
        plugin_id: str,
        tenant_id: UUID,
        request_id: str | None = None,
    ) -> PluginContext:
        plugin = self._registry.get(plugin_id)
        if plugin is None:
            raise KeyError(f"plugin {plugin_id!r} not registered")

        # Credential scope: tenant_id or team_id depending on declaration
        # For v1, credential_scope == "tenant" → use tenant_id
        # If scope == "team", we'd need a team_id here; defer that path
        if plugin.credential_scope != "tenant":
            raise NotImplementedError(
                f"credential_scope={plugin.credential_scope!r} not yet supported"
            )
        scope_id = tenant_id

        # Plugin's config: if get_config_schema returns a class, instantiate from
        # env or yield a default. For v1, just use the schema's defaults.
        config_cls = plugin.get_config_schema()
        config = config_cls() if config_cls else _EmptyConfig()

        return PluginContext(
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            request_id=request_id or str(uuid4()),
            pool=self._pool,
            memories=cast(
                MemoryClient,
                MemoryClientImpl(self._pool, tenant_id, plugin_id, self._deps, self._identity_id),
            ),
            notices=cast(NoticeClient, NoticeClientImpl(self._pool, tenant_id, plugin_id)),
            scheduler=cast(JobScheduler, JobSchedulerImpl(plugin_id)),
            credentials=cast(
                CredentialStore, CredentialStoreImpl(self._pool, self._vault, scope_id, plugin_id)
            ),
            config=config,
            audit=self._audit,
            permissions=cast(PermissionResolver, PermissionResolverImpl(self._pool, tenant_id)),
        )


class _EmptyConfig(BaseModel):
    """Default config when plugin doesn't declare one."""

    pass
