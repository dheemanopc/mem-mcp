"""Concrete CredentialStore — wraps SkillVault for plugin use.

Plugin owns the shape (any JSON-serializable dict). Core owns the crypto
(AES-GCM with AAD binding) and the storage table.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]

from mem_mcp.db.tenant_tx import system_tx
from mem_mcp.skills.vault import SkillVault


class CredentialStoreImpl:
    """Plugin-scoped credential storage. The plugin can ONLY see its own creds."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        vault: SkillVault,
        scope_id: UUID,  # tenant_id OR team_id depending on credential_scope
        plugin_id: str,
    ) -> None:
        self._pool = pool
        self._vault = vault
        self._scope_id = scope_id
        self._plugin_id = plugin_id

    async def store(self, creds: dict[str, Any]) -> None:
        """Store encrypted credentials scoped to (scope_id, plugin_id)."""
        async with system_tx(self._pool) as conn:
            await self._vault.store(conn, self._scope_id, self._plugin_id, creds)

    async def load(self) -> dict[str, Any]:
        """Load decrypted credentials, or empty dict if never set."""
        async with system_tx(self._pool) as conn:
            return await self._vault.load(conn, self._scope_id, self._plugin_id)

    async def delete(self) -> bool:
        """Delete stored credentials. Returns True if something was deleted."""
        async with system_tx(self._pool) as conn:
            return await self._vault.delete(conn, self._scope_id, self._plugin_id)
