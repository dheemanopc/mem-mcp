"""Extend teams_select RLS so creators see their own teams.

Per owner decision memsys:5d3247db (Reading A) + bug memsys:f01247ad:
the existing `teams_select` policy only matched rows in
`current_user_active_team_ids()` — which is empty at the moment of
`INSERT ... RETURNING`, because the creator hasn't yet been added as a
member. The freshly-inserted row therefore fails the SELECT-side check
on the RETURNING, surfacing as "new row violates row-level security
policy for table teams" even though the WITH CHECK had passed.

Fix: extend the USING expression to also match rows where
`created_by_tenant_id = current_setting('app.current_tenant_id')::uuid`.
Creators can always read their own teams. The next step in the same flow
(adding the creator as owner in `team_role_assignments`) brings them
into `current_user_active_team_ids()`; the override is only load-bearing
for the transient gap.

Revision ID: 0027_teams_select_creator
Revises: 0026_personal_teams_widen
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0027_teams_select_creator"
down_revision = "0026_personal_teams_widen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS teams_select ON teams"))
    op.execute(
        sa.text("""
        CREATE POLICY teams_select ON teams FOR SELECT TO mem_app
        USING (
            deleted_at IS NULL AND (
                id = ANY (current_user_active_team_ids())
                OR created_by_tenant_id = current_setting(
                    'app.current_tenant_id', true
                )::uuid
            )
        )
    """)
    )


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS teams_select ON teams"))
    op.execute(
        sa.text("""
        CREATE POLICY teams_select ON teams FOR SELECT TO mem_app
        USING (
            deleted_at IS NULL
            AND id = ANY (current_user_active_team_ids())
        )
    """)
    )
