"""Backfill personal teams for tenants provisioned after migration 0026.

0026 gave every then-existing tenant a personal team, an owner role assignment,
and ``tenant_identities.default_team_id``. But ``jobs.reconcile_signups`` — the
path that provisions real prod signups — never created any of the three, so
every tenant born after 0026 was unusable on MCP: no team resolves and the user
holds no role, so calls fail with ``team_required`` / "caller lacks required
permission".

Found 2026-08-11: two beta users could complete Google signup but not log in.
The code path is fixed in ``identity.personal_team.ensure_personal_team``, now
shared by both provisioning callers; this migration repairs the tenants already
created without it.

Idempotent and safe to re-run: both statements are guarded (``NOT EXISTS`` on a
live personal team, ``default_team_id IS NULL``), so tenants already correct are
untouched. Same SQL shape as the 0026 backfill.

Revision ID: 0043_backfill_personal_teams
Revises: 0042_grant_operator_system_admin
Create Date: 2026-08-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0043_backfill_personal_teams"
down_revision = "0042_grant_operator_system_admin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Personal team + owner role assignment for any tenant lacking one.
    op.execute(
        sa.text("""
        WITH new_personal_teams AS (
            INSERT INTO teams (name, created_by_tenant_id, team_type)
            SELECT
                'Personal — ' || COALESCE(t.email, t.id::text) AS name,
                t.id,
                'personal'
            FROM tenants t
            WHERE NOT EXISTS (
                SELECT 1 FROM teams existing
                WHERE existing.created_by_tenant_id = t.id
                  AND existing.team_type = 'personal'
                  AND existing.deleted_at IS NULL
            )
            RETURNING id, created_by_tenant_id
        )
        INSERT INTO team_role_assignments
            (parent_team_id, member_kind, member_id, role_id, status, assigned_by_user_id)
        SELECT
            npt.id, 'user', npt.created_by_tenant_id,
            (SELECT id FROM roles_catalog WHERE role_key='owner' AND plugin_id IS NULL),
            'active', npt.created_by_tenant_id
        FROM new_personal_teams npt
    """)
    )

    # Point primary identities lacking a default at their tenant's personal team.
    op.execute(
        sa.text("""
        UPDATE tenant_identities ti
        SET default_team_id = (
            SELECT id FROM teams t
            WHERE t.created_by_tenant_id = ti.tenant_id
              AND t.team_type = 'personal'
              AND t.deleted_at IS NULL
            ORDER BY t.created_at ASC LIMIT 1
        )
        WHERE ti.is_primary AND ti.default_team_id IS NULL
    """)
    )


def downgrade() -> None:
    # Intentionally a no-op, matching 0026's downgrade. Deleting the teams would
    # orphan any memories written into them, and clearing default_team_id would
    # re-break the affected users. There is no safe automatic reversal.
    pass
