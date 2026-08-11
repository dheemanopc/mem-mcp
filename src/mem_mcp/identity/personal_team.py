"""Personal-team provisioning — the one place a tenant's team-of-one is created.

Every tenant needs a personal team, an owner role assignment on it, and
``tenant_identities.default_team_id`` pointing at it. Without all three, MCP is
unusable for that user: ``memory_*`` calls resolve no team and the caller holds
no role, so they fail with ``team_required`` / "caller lacks required
permission".

Migration 0026 backfilled this for every tenant existing at the time, but the
two runtime provisioning paths diverged afterwards:

* ``auth.middleware`` (localdev auto-create) called it.
* ``jobs.reconcile_signups`` — the path that actually provisions real prod
  signups — did NOT, so every tenant it created was born unusable. Found
  2026-08-11 after two beta users could sign up but not log in.

Both callers now share this function so they cannot drift again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


async def ensure_personal_team(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    email: str,
    cognito_sub: str | None = None,
) -> UUID | None:
    """Create the personal team for ``tenant_id`` and point the identity at it.

    Creates the team, grants the tenant the ``owner`` role on it, and sets
    ``default_team_id`` on the tenant's primary identity.

    Idempotent by guard, not by constraint: ``teams.name`` has no UNIQUE, so a
    second call would create a duplicate team. Returns ``None`` without writing
    when the tenant already has a live personal team. Mirrors the migration 0026
    backfill shape.

    RLS: ``teams_insert`` requires ``created_by_tenant_id =
    safe_current_tenant_id()``. Under ``system_tx`` the GUC is unset and the
    INSERT fails with InsufficientPrivilegeError, so SET LOCAL it for the
    duration of the transaction. Same pattern as PR #305 for the
    references-validator path.

    Args:
        conn: connection inside an open transaction.
        tenant_id: tenant to provision.
        email: used for the team's display name.
        cognito_sub: when given, upserts the identity row (localdev auto-create,
            where the identity may not exist yet). When ``None``, only updates
            the existing primary identity — the reconcile path, which has
            already inserted it.

    Returns:
        The new team's id, or ``None`` if one already existed.
    """
    existing = await conn.fetchval(
        "SELECT id FROM teams "
        "WHERE created_by_tenant_id = $1 AND team_type = 'personal' "
        "  AND deleted_at IS NULL "
        "ORDER BY created_at ASC LIMIT 1",
        tenant_id,
    )
    if existing is not None:
        return None

    await conn.execute(
        "SELECT set_config('app.current_tenant_id', $1, true)",
        str(tenant_id),
    )
    new_team_id = await conn.fetchval(
        "INSERT INTO teams (name, created_by_tenant_id, team_type) "
        "VALUES ($1, $2, 'personal') RETURNING id",
        f"Personal — {email}",
        tenant_id,
    )
    owner_role_id = await conn.fetchval(
        "SELECT id FROM roles_catalog WHERE role_key = 'owner' AND plugin_id IS NULL"
    )
    if owner_role_id is not None:
        await conn.execute(
            "INSERT INTO team_role_assignments "
            "(parent_team_id, member_kind, member_id, role_id, status, "
            " assigned_by_user_id) "
            "VALUES ($1, 'user', $2, $3, 'active', $2) "
            "ON CONFLICT DO NOTHING",
            new_team_id,
            tenant_id,
            owner_role_id,
        )

    if cognito_sub is not None:
        await conn.execute(
            "INSERT INTO tenant_identities "
            "(tenant_id, cognito_sub, provider, email, is_primary, default_team_id) "
            "VALUES ($1, $2, 'cognito', $3, true, $4) "
            "ON CONFLICT (cognito_sub) DO UPDATE SET "
            " default_team_id = EXCLUDED.default_team_id "
            "WHERE tenant_identities.default_team_id IS NULL",
            tenant_id,
            cognito_sub,
            email,
            new_team_id,
        )
    else:
        await conn.execute(
            "UPDATE tenant_identities SET default_team_id = $2 "
            "WHERE tenant_id = $1 AND is_primary AND default_team_id IS NULL",
            tenant_id,
            new_team_id,
        )

    return UUID(str(new_team_id)) if new_team_id is not None else None
