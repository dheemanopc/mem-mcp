"""Per-plugin Postgres schema creation.

Each plugin gets its own schema `plugin_<id>` (sanitized). This isolates
plugin tables from core (and from each other). Schema is created at lifespan
startup with grants for mem_app and mem_maint.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import asyncpg  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from mem_mcp.plugins.registry import PluginRegistry

log = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[a-z0-9_]+$")


def schema_name_for(plugin_id: str) -> str:
    """Return the PG schema name for a plugin. Sanitizes kebab to snake."""
    safe = plugin_id.replace("-", "_").lower()
    if not _SAFE_ID.match(safe):
        raise ValueError(f"plugin_id {plugin_id!r} cannot be mapped to a safe PG schema name")
    return f"plugin_{safe}"


async def ensure_plugin_schemas(pool: asyncpg.Pool, registry: PluginRegistry) -> None:
    """Create per-plugin schemas if missing. Idempotent."""
    for plugin in registry.all():
        schema = schema_name_for(plugin.id)
        async with pool.acquire() as conn:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            await conn.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO mem_app')
            await conn.execute(f'GRANT ALL ON SCHEMA "{schema}" TO mem_maint')
        log.info("plugin_schema_ensured", extra={"plugin_id": plugin.id, "schema": schema})
