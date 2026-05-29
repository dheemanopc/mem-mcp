"""Role catalog accessors. Seed lives in migration 0021 (DB-side, idempotent)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


class RoleNotFoundError(Exception):
    """Role with the given (plugin_id, role_key) does not exist or is inactive."""


CORE_ROLE_KEYS: frozenset[str] = frozenset(
    {"owner", "admin", "member", "viewer", "vendor", "customer", "service-bot"}
)


async def get_role_by_key(
    conn: asyncpg.Connection, role_key: str, plugin_id: str | None = None
) -> dict[str, Any]:
    """Fetch a role from roles_catalog by (plugin_id, role_key).

    Args:
        plugin_id: None for core roles; the plugin's id for plugin-defined roles.

    Raises:
        RoleNotFoundError: if not found or inactive.
    """
    row = await conn.fetchrow(
        """
        SELECT id, role_key, display_name, role_class, plugin_id, permissions, is_active
        FROM roles_catalog
        WHERE role_key = $1
          AND plugin_id IS NOT DISTINCT FROM $2
          AND is_active = true
        """,
        role_key,
        plugin_id,
    )
    if row is None:
        raise RoleNotFoundError(f"role not found: role_key={role_key!r} plugin_id={plugin_id!r}")
    return dict(row)
