"""RLS: Use SECURITY DEFINER helpers to break team_members policy recursion.

Revision ID: 0016_rls_security_definer
Revises: 0015_enterprise_phase1a
Create Date: 2026-05-25

URGENT PROD FIX.

PR #238 introduced RLS policies on team_members that reference team_members
itself inside the USING clause, triggering PostgreSQL's infinite-recursion
detection. Every memory_* MCP call fails with `-32603 internal error: infinite
recursion detected in policy for relation "team_members"`.

Solution: Wrap membership lookups in SECURITY DEFINER functions that bypass
RLS, then recreate the affected policies to call these functions instead of
inlining the recursive subqueries.

Affected:
- team_members SELECT/INSERT/UPDATE/DELETE policies
- teams SELECT/UPDATE/DELETE policies
- memories SELECT policy (queries team_members)
"""

import sqlalchemy as sa

from alembic import op

revision = "0016_rls_security_definer"
down_revision = "0015_enterprise_phase1a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create SECURITY DEFINER functions and fix RLS policies."""

    op.execute(
        sa.text("""
-- SECURITY DEFINER helpers — bypass RLS so policies on team_members
-- can reference team_members without recursion.

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

GRANT EXECUTE ON FUNCTION current_user_active_team_ids() TO mem_app;
GRANT EXECUTE ON FUNCTION current_user_admin_team_ids() TO mem_app;
        """)
    )

    # Drop + recreate teams policies
    op.execute(sa.text("DROP POLICY IF EXISTS teams_select ON teams"))
    op.execute(
        sa.text("""
CREATE POLICY teams_select ON teams FOR SELECT TO mem_app
    USING (
        deleted_at IS NULL
        AND id = ANY(current_user_active_team_ids())
    )
        """)
    )

    op.execute(sa.text("DROP POLICY IF EXISTS teams_update ON teams"))
    op.execute(
        sa.text("""
CREATE POLICY teams_update ON teams FOR UPDATE TO mem_app
    USING (id = ANY(current_user_admin_team_ids()))
        """)
    )

    op.execute(sa.text("DROP POLICY IF EXISTS teams_delete ON teams"))
    op.execute(
        sa.text("""
CREATE POLICY teams_delete ON teams FOR DELETE TO mem_app
    USING (id = ANY(current_user_admin_team_ids()))
        """)
    )

    # Drop + recreate team_members policies
    op.execute(sa.text("DROP POLICY IF EXISTS team_members_select ON team_members"))
    op.execute(
        sa.text("""
CREATE POLICY team_members_select ON team_members FOR SELECT TO mem_app
    USING (
        tenant_id = current_setting('app.current_tenant_id', true)::uuid
        OR team_id = ANY(current_user_active_team_ids())
    )
        """)
    )

    op.execute(sa.text("DROP POLICY IF EXISTS team_members_insert ON team_members"))
    op.execute(
        sa.text("""
CREATE POLICY team_members_insert ON team_members FOR INSERT TO mem_app
    WITH CHECK (
        added_by_tenant_id = current_setting('app.current_tenant_id', true)::uuid
        AND team_id = ANY(current_user_admin_team_ids())
    )
        """)
    )

    op.execute(sa.text("DROP POLICY IF EXISTS team_members_update ON team_members"))
    op.execute(
        sa.text("""
CREATE POLICY team_members_update ON team_members FOR UPDATE TO mem_app
    USING (team_id = ANY(current_user_admin_team_ids()))
        """)
    )

    op.execute(sa.text("DROP POLICY IF EXISTS team_members_delete ON team_members"))
    op.execute(
        sa.text("""
CREATE POLICY team_members_delete ON team_members FOR DELETE TO mem_app
    USING (
        tenant_id = current_setting('app.current_tenant_id', true)::uuid
        OR team_id = ANY(current_user_admin_team_ids())
    )
        """)
    )

    # Drop + recreate memories_select policy
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


def downgrade() -> None:
    """Revert to previous policy state (pre-SECURITY DEFINER)."""

    # Drop the SECURITY DEFINER functions
    op.execute(
        sa.text("""
DROP FUNCTION IF EXISTS current_user_admin_team_ids() CASCADE;
DROP FUNCTION IF EXISTS current_user_active_team_ids() CASCADE;
        """)
    )

    # Restore original (buggy) policies from 0015.
    # For downgrade purposes, we restore the exact policies from 0015
    # even though they have the recursion bug. This is standard alembic
    # behavior (downgrade should undo the changes, not fix them).

    # teams policies
    op.execute(sa.text("DROP POLICY IF EXISTS teams_select ON teams"))
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

    op.execute(sa.text("DROP POLICY IF EXISTS teams_update ON teams"))
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

    op.execute(sa.text("DROP POLICY IF EXISTS teams_delete ON teams"))
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

    # team_members policies
    op.execute(sa.text("DROP POLICY IF EXISTS team_members_select ON team_members"))
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

    op.execute(sa.text("DROP POLICY IF EXISTS team_members_insert ON team_members"))
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

    op.execute(sa.text("DROP POLICY IF EXISTS team_members_update ON team_members"))
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

    op.execute(sa.text("DROP POLICY IF EXISTS team_members_delete ON team_members"))
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

    # memories_select policy
    op.execute(sa.text("DROP POLICY IF EXISTS memories_select ON memories"))
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
