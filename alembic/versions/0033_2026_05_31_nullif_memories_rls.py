"""Defense-in-depth: NULLIF the GUC cast in memories_select + team helper functions.

Same defect cluster as migrations 0029-0031 on async_write_queue:
`current_setting('app.current_tenant_id', true)::uuid` crashes with
`invalid input syntax for type uuid: ""` when the GUC is '' — which
happens on any mem_app pool connection recycled from a previously
rolled-back tenant_tx (PG custom-GUC quirk: SET LOCAL + ROLLBACK
resets to '', not NULL).

This migration is defense-in-depth. The acute bug — memory_get_batch
slug-tuple crash — is fixed at the application layer by routing the
memories fetch through tenant_tx so the GUC always holds a real UUID.
But the policy + helper functions still bare-cast, and any other code
path that touches `memories` or these helpers under system_tx (or any
empty-GUC context) would crash the same way.

NULLIF-wrap the casts so '' coerces to NULL before the cast. NULL ::uuid
is safe. The policies/functions then return their "no GUC set" outcome
(no rows / empty array) rather than crashing.

Audited sites today (per [[407fbf20]] cluster):
  - memories_select policy (this migration)
  - current_user_active_team_ids() SECURITY DEFINER fn (this migration)
  - current_user_admin_team_ids() SECURITY DEFINER fn (this migration)
  - async_write_queue policies (fixed in 0031)
  - create_team (fixed earlier today, PR #287)

Revision ID: 0033_nullif_memories_rls
Revises: 0032_aq_caller_identity
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0033_nullif_memories_rls"
down_revision = "0032_aq_caller_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SECURITY DEFINER helpers (originally migration 0016).
    op.execute(
        sa.text("""
CREATE OR REPLACE FUNCTION current_user_active_team_ids()
RETURNS uuid[]
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT COALESCE(array_agg(team_id), ARRAY[]::uuid[])
    FROM team_members
    WHERE tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
      AND status = 'active'
$$;
        """)
    )

    op.execute(
        sa.text("""
CREATE OR REPLACE FUNCTION current_user_admin_team_ids()
RETURNS uuid[]
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT COALESCE(array_agg(team_id), ARRAY[]::uuid[])
    FROM team_members
    WHERE tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
      AND role = 'admin'
      AND status = 'active'
$$;
        """)
    )

    # memories_select policy (originally migration 0016).
    op.execute(sa.text("DROP POLICY IF EXISTS memories_select ON memories"))
    op.execute(
        sa.text("""
CREATE POLICY memories_select ON memories FOR SELECT TO mem_app
    USING (
        tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        OR (
            visibility = 'team'
            AND team_id = ANY(current_user_active_team_ids())
        )
    )
        """)
    )


def downgrade() -> None:
    # Restore the bare-cast versions (matches what migration 0016 originally
    # created). NOT recommended — these crash under empty GUC.
    op.execute(
        sa.text("""
CREATE OR REPLACE FUNCTION current_user_active_team_ids()
RETURNS uuid[]
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT COALESCE(array_agg(team_id), ARRAY[]::uuid[])
    FROM team_members
    WHERE tenant_id = current_setting('app.current_tenant_id', true)::uuid
      AND status = 'active'
$$;
        """)
    )

    op.execute(
        sa.text("""
CREATE OR REPLACE FUNCTION current_user_admin_team_ids()
RETURNS uuid[]
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT COALESCE(array_agg(team_id), ARRAY[]::uuid[])
    FROM team_members
    WHERE tenant_id = current_setting('app.current_tenant_id', true)::uuid
      AND role = 'admin'
      AND status = 'active'
$$;
        """)
    )

    op.execute(sa.text("DROP POLICY IF EXISTS memories_select ON memories"))
    op.execute(
        sa.text("""
CREATE POLICY memories_select ON memories FOR SELECT TO mem_app
    USING (
        tenant_id = current_setting('app.current_tenant_id', true)::uuid
        OR (
            visibility = 'team'
            AND team_id = ANY(current_user_active_team_ids())
        )
    )
        """)
    )
