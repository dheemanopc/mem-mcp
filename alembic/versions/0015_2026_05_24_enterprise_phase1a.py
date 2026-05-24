"""Enterprise Phase 1A: Teams schema, RLS policy, custom:google_hd plumbing.

Revision ID: 0015_enterprise_phase1a
Revises: 0014_embedding_status
Create Date: 2026-05-24

This migration ships the foundational schema for team/enterprise features:

1. New tables: teams, team_members
   - teams: workspace-domain uniqueness constraint (per memory 6dde6ef2)
   - team_members: role-based (admin/member/viewer), status tracking

2. New columns on memories:
   - visibility: 'private' (default) | 'team'
   - team_id: UUID ref to teams (nullable)
   - Consistency check: visibility='team' iff team_id IS NOT NULL

3. New columns on tenant_identities:
   - workspace_domain: TEXT NULL (extracted from custom:google_hd claim)
   - default_team_id: UUID NULL (session default; agent holds this per conversation)

4. RLS policy updates:
   - teams: SELECT via active team membership, INSERT (creator), UPDATE/DELETE (admin only)
   - team_members: SELECT via self/team membership, INSERT/UPDATE (admin only), DELETE (self or admin)
   - memories: Extend SELECT to allow team-visible memories via active team membership

5. Auth callback (/auth/callback in routes.py):
   - Extract custom:google_hd from ID token claim
   - Persist as tenant_identities.workspace_domain on existing-user UPDATE
   - New-user path handled separately (or backfilled on next login)
"""

import sqlalchemy as sa

from alembic import op

revision = "0015_enterprise_phase1a"
down_revision = "0014_embedding_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply enterprise phase 1A schema."""

    # ==========================================================================
    # NEW TABLE: teams
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE TABLE teams (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            workspace_domain TEXT NULL,
            created_by_tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            deleted_at TIMESTAMPTZ NULL
        )
        """)
    )

    # Workspace-uniqueness: one workspace team per hd domain (or none for personal teams)
    op.execute(
        sa.text("""
        CREATE UNIQUE INDEX teams_workspace_domain_unique
            ON teams(workspace_domain)
            WHERE workspace_domain IS NOT NULL AND deleted_at IS NULL
        """)
    )

    op.execute(
        sa.text("""
        CREATE INDEX teams_created_by_idx ON teams(created_by_tenant_id)
            WHERE deleted_at IS NULL
        """)
    )

    # ==========================================================================
    # NEW TABLE: team_members
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE TABLE team_members (
            team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('admin', 'member', 'viewer')),
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'pending', 'revoked')),
            invited_email TEXT NULL,
            added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            added_by_tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            PRIMARY KEY (team_id, tenant_id)
        )
        """)
    )

    op.execute(
        sa.text("""
        CREATE INDEX team_members_tenant_idx ON team_members(tenant_id)
            WHERE status = 'active'
        """)
    )

    op.execute(
        sa.text("""
        CREATE INDEX team_members_pending_email_idx ON team_members(invited_email)
            WHERE status = 'pending'
        """)
    )

    # ==========================================================================
    # NEW COLUMNS on memories: visibility, team_id
    # ==========================================================================
    op.execute(
        sa.text("""
        ALTER TABLE memories
            ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'
                CHECK (visibility IN ('private', 'team'))
        """)
    )

    op.execute(
        sa.text("""
        ALTER TABLE memories
            ADD COLUMN team_id UUID NULL REFERENCES teams(id) ON DELETE SET NULL
        """)
    )

    # Consistency constraint: visibility='team' iff team_id IS NOT NULL
    op.execute(
        sa.text("""
        ALTER TABLE memories
            ADD CONSTRAINT memories_visibility_team_consistency
                CHECK ((visibility = 'private' AND team_id IS NULL)
                    OR (visibility = 'team' AND team_id IS NOT NULL))
        """)
    )

    # ==========================================================================
    # NEW COLUMNS on tenant_identities
    # ==========================================================================
    op.execute(
        sa.text("""
        ALTER TABLE tenant_identities
            ADD COLUMN workspace_domain TEXT NULL
        """)
    )

    op.execute(
        sa.text("""
        ALTER TABLE tenant_identities
            ADD COLUMN default_team_id UUID NULL REFERENCES teams(id) ON DELETE SET NULL
        """)
    )

    # ==========================================================================
    # GRANTS to mem_app role
    # ==========================================================================
    op.execute(sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON teams TO mem_app"))
    op.execute(sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON team_members TO mem_app"))

    # ==========================================================================
    # RLS: Enable on new tables
    # ==========================================================================
    op.execute(sa.text("ALTER TABLE teams ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE teams FORCE ROW LEVEL SECURITY"))

    op.execute(sa.text("ALTER TABLE team_members ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE team_members FORCE ROW LEVEL SECURITY"))

    # ==========================================================================
    # RLS POLICIES: teams
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE POLICY teams_select ON teams FOR SELECT TO mem_app
            USING (
                deleted_at IS NULL
                AND id IN (
                    SELECT team_id FROM team_members
                    WHERE tenant_id = current_setting('app.current_tenant_id', true)::uuid
                      AND status = 'active'
                )
            )
        """)
    )

    op.execute(
        sa.text("""
        CREATE POLICY teams_insert ON teams FOR INSERT TO mem_app
            WITH CHECK (created_by_tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """)
    )

    op.execute(
        sa.text("""
        CREATE POLICY teams_update ON teams FOR UPDATE TO mem_app
            USING (
                id IN (
                    SELECT team_id FROM team_members
                    WHERE tenant_id = current_setting('app.current_tenant_id', true)::uuid
                      AND role = 'admin'
                      AND status = 'active'
                )
            )
        """)
    )

    op.execute(
        sa.text("""
        CREATE POLICY teams_delete ON teams FOR DELETE TO mem_app
            USING (
                id IN (
                    SELECT team_id FROM team_members
                    WHERE tenant_id = current_setting('app.current_tenant_id', true)::uuid
                      AND role = 'admin'
                      AND status = 'active'
                )
            )
        """)
    )

    # ==========================================================================
    # RLS POLICIES: team_members
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE POLICY team_members_select ON team_members FOR SELECT TO mem_app
            USING (
                tenant_id = current_setting('app.current_tenant_id', true)::uuid
                OR team_id IN (
                    SELECT team_id FROM team_members
                    WHERE tenant_id = current_setting('app.current_tenant_id', true)::uuid
                      AND status = 'active'
                )
            )
        """)
    )

    op.execute(
        sa.text("""
        CREATE POLICY team_members_insert ON team_members FOR INSERT TO mem_app
            WITH CHECK (
                added_by_tenant_id = current_setting('app.current_tenant_id', true)::uuid
                AND team_id IN (
                    SELECT team_id FROM team_members
                    WHERE tenant_id = current_setting('app.current_tenant_id', true)::uuid
                      AND role = 'admin'
                      AND status = 'active'
                )
            )
        """)
    )

    op.execute(
        sa.text("""
        CREATE POLICY team_members_update ON team_members FOR UPDATE TO mem_app
            USING (
                team_id IN (
                    SELECT team_id FROM team_members
                    WHERE tenant_id = current_setting('app.current_tenant_id', true)::uuid
                      AND role = 'admin'
                      AND status = 'active'
                )
            )
        """)
    )

    op.execute(
        sa.text("""
        CREATE POLICY team_members_delete ON team_members FOR DELETE TO mem_app
            USING (
                tenant_id = current_setting('app.current_tenant_id', true)::uuid
                OR team_id IN (
                    SELECT team_id FROM team_members
                    WHERE tenant_id = current_setting('app.current_tenant_id', true)::uuid
                      AND role = 'admin'
                      AND status = 'active'
                )
            )
        """)
    )

    # ==========================================================================
    # RLS POLICY UPDATE: memories SELECT to allow team visibility
    # ==========================================================================
    # Drop the existing tenant-isolation policy and replace with extended one
    op.execute(
        sa.text("""
        DROP POLICY memories_tenant_isolation ON memories
        """)
    )

    # New SELECT policy: own memories OR team-visible via active team membership
    op.execute(
        sa.text("""
        CREATE POLICY memories_select ON memories FOR SELECT TO mem_app
            USING (
                tenant_id = current_setting('app.current_tenant_id', true)::uuid
                OR (
                    visibility = 'team'
                    AND team_id IN (
                        SELECT team_id FROM team_members
                        WHERE tenant_id = current_setting('app.current_tenant_id', true)::uuid
                          AND status = 'active'
                    )
                )
            )
        """)
    )

    # INSERT/UPDATE/DELETE: owner only (unchanged from original isolation policy)
    op.execute(
        sa.text("""
        CREATE POLICY memories_insert ON memories FOR INSERT TO mem_app
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """)
    )

    op.execute(
        sa.text("""
        CREATE POLICY memories_update ON memories FOR UPDATE TO mem_app
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """)
    )

    op.execute(
        sa.text("""
        CREATE POLICY memories_delete ON memories FOR DELETE TO mem_app
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """)
    )


def downgrade() -> None:
    """Revert enterprise phase 1A schema."""
    # Drop the new RLS policies on memories first
    op.execute(sa.text("DROP POLICY IF EXISTS memories_select ON memories"))
    op.execute(sa.text("DROP POLICY IF EXISTS memories_insert ON memories"))
    op.execute(sa.text("DROP POLICY IF EXISTS memories_update ON memories"))
    op.execute(sa.text("DROP POLICY IF EXISTS memories_delete ON memories"))

    # Restore the original tenant-isolation policy
    op.execute(
        sa.text("""
        CREATE POLICY memories_tenant_isolation ON memories
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """)
    )

    # Drop RLS policies on new tables
    op.execute(sa.text("DROP POLICY IF EXISTS teams_select ON teams"))
    op.execute(sa.text("DROP POLICY IF EXISTS teams_insert ON teams"))
    op.execute(sa.text("DROP POLICY IF EXISTS teams_update ON teams"))
    op.execute(sa.text("DROP POLICY IF EXISTS teams_delete ON teams"))

    op.execute(sa.text("DROP POLICY IF EXISTS team_members_select ON team_members"))
    op.execute(sa.text("DROP POLICY IF EXISTS team_members_insert ON team_members"))
    op.execute(sa.text("DROP POLICY IF EXISTS team_members_update ON team_members"))
    op.execute(sa.text("DROP POLICY IF EXISTS team_members_delete ON team_members"))

    # Disable RLS on new tables
    op.execute(sa.text("ALTER TABLE teams DISABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE team_members DISABLE ROW LEVEL SECURITY"))

    # Drop columns from existing tables
    op.execute(
        sa.text("""
        ALTER TABLE memories
            DROP CONSTRAINT IF EXISTS memories_visibility_team_consistency,
            DROP COLUMN IF EXISTS team_id,
            DROP COLUMN IF EXISTS visibility
        """)
    )

    op.execute(
        sa.text("""
        ALTER TABLE tenant_identities
            DROP COLUMN IF EXISTS default_team_id,
            DROP COLUMN IF EXISTS workspace_domain
        """)
    )

    # Drop new tables
    op.execute(sa.text("DROP TABLE IF EXISTS team_members"))
    op.execute(sa.text("DROP TABLE IF EXISTS teams"))
