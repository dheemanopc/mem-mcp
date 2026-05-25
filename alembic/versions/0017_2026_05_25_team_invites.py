"""Enterprise Phase 1B: Team invites table, drop unused team_members column.

Revision ID: 0017_team_invites
Revises: 0016_rls_security_definer
Create Date: 2026-05-25

This migration adds support for pending email-based team invites:

1. New table: team_invites
   - Tracks pending invitations by email
   - Linked to teams and invited_by tenant
   - Status: pending, accepted, cancelled

2. Schema cleanup:
   - Drop the unused team_members.invited_email column and its index from PR 1.A
   - Invite flow now goes through team_invites table with proper tracking

3. RLS policies:
   - Use SECURITY DEFINER helper functions from 0016 to avoid recursion
   - SELECT: user can see invites for teams they are active members of
   - INSERT/UPDATE/DELETE: user must be admin in the team
"""

import sqlalchemy as sa

from alembic import op

revision = "0017_team_invites"
down_revision = "0016_rls_security_definer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply enterprise phase 1B schema."""

    # ==========================================================================
    # NEW TABLE: team_invites
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE TABLE team_invites (
            team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            email TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin','member','viewer')) DEFAULT 'member',
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','cancelled')),
            invited_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            invited_by_tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            PRIMARY KEY (team_id, email)
        )
        """)
    )

    op.execute(
        sa.text("""
        CREATE INDEX team_invites_email_idx ON team_invites(LOWER(email)) WHERE status='pending'
        """)
    )

    # ==========================================================================
    # GRANTS to mem_app role
    # ==========================================================================
    op.execute(sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON team_invites TO mem_app"))

    # ==========================================================================
    # RLS: Enable on team_invites
    # ==========================================================================
    op.execute(sa.text("ALTER TABLE team_invites ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE team_invites FORCE ROW LEVEL SECURITY"))

    # ==========================================================================
    # RLS POLICIES: team_invites
    # ==========================================================================
    # SELECT: user can see invites for teams they are an active member of
    # Uses SECURITY DEFINER helper to avoid recursion on team_members
    op.execute(
        sa.text("""
        CREATE POLICY team_invites_select ON team_invites FOR SELECT TO mem_app
            USING (team_id = ANY(current_user_active_team_ids()))
        """)
    )

    # INSERT/UPDATE/DELETE: user must be admin in the team
    # Uses SECURITY DEFINER helper to avoid recursion on team_members
    op.execute(
        sa.text("""
        CREATE POLICY team_invites_modify ON team_invites FOR ALL TO mem_app
            USING (team_id = ANY(current_user_admin_team_ids()))
        """)
    )

    # ==========================================================================
    # CLEANUP: Drop unused columns from team_members (from PR 1.A)
    # ==========================================================================
    op.execute(sa.text("DROP INDEX IF EXISTS team_members_pending_email_idx"))
    op.execute(
        sa.text("""
        ALTER TABLE team_members DROP COLUMN IF EXISTS invited_email
        """)
    )


def downgrade() -> None:
    """Revert enterprise phase 1B schema."""
    # Re-add the column/index to team_members
    op.execute(
        sa.text("""
        ALTER TABLE team_members ADD COLUMN invited_email TEXT NULL
        """)
    )

    op.execute(
        sa.text("""
        CREATE INDEX team_members_pending_email_idx ON team_members(invited_email)
            WHERE status = 'pending'
        """)
    )

    # Drop RLS policies on team_invites
    op.execute(sa.text("DROP POLICY IF EXISTS team_invites_select ON team_invites"))
    op.execute(sa.text("DROP POLICY IF EXISTS team_invites_modify ON team_invites"))

    # Disable RLS on team_invites
    op.execute(sa.text("ALTER TABLE team_invites DISABLE ROW LEVEL SECURITY"))

    # Drop the new table
    op.execute(sa.text("DROP TABLE IF EXISTS team_invites"))
