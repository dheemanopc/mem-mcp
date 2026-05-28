"""RLS helper functions: handle empty-string GUC gracefully.

The 0016 SECURITY DEFINER helpers cast current_setting('app.current_tenant_id', true)
directly to uuid. When called from system_tx (which never sets the GUC) the value
can be empty string, and ''::uuid throws InvalidTextRepresentationError. This
crashes /auth/callback for any user whose Cognito session triggers the team_invites
SELECT inside system_tx.

Fix: wrap with NULLIF(..., '') so empty string becomes NULL, the cast yields NULL,
and the equality check `tenant_id = NULL` is silently false (no rows match,
no crash).

Revision ID: 0019_nullif_empty_tenant
Revises: 0018_tenant_system_roles
Create Date: 2026-05-28
"""

import sqlalchemy as sa

from alembic import op

revision = "0019_nullif_empty_tenant"
down_revision = "0018_tenant_system_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Redefine SECURITY DEFINER functions with NULLIF guard."""

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


def downgrade() -> None:
    """Revert to original function definitions (without NULLIF guard)."""

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
