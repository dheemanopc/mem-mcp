"""Personal teams backfill + visibility widen + fragment_id column.

Per memsys foundation 1c47bd99 + test plan e552d271 Spec 2 + ratification A3
(safe default: every existing memory → personal team-of-one; bucketing tool
ships separately later as an owner-run command).

Three coupled changes (single migration; rollback also single-step):
1. Personal teams backfill: create a personal team per tenant; set
   tenant_identities.default_team_id.
2. Visibility enum widen: drop CHECK (visibility IN ('private','team')) and
   add CHECK (visibility IN ('team','external','public')). Migrate existing
   rows: visibility='private' → 'team' scoped to author's personal team.
3. memories.fragment_id INT NULL added for thread-reply addressing.

Revision ID: 0026_personal_teams_widen
Revises: 0025_memory_references
Create Date: 2026-05-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0026_personal_teams_widen"
down_revision = "0025_memory_references"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================
    # 1. Personal teams backfill for every existing tenant
    # =========================================================
    # Create personal team per tenant that doesn't already have one
    # (e.g., manually-created earlier from web UI).
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

    # Populate tenant_identities.default_team_id for primary identities lacking one,
    # pointing at the tenant's personal team.
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

    # =========================================================
    # 2. Visibility widen
    # =========================================================
    # Order matters: drop the old CHECK constraints FIRST. The legacy
    # memories_visibility_team_consistency forbids (visibility='private'
    # AND team_id IS NOT NULL), so backfilling team_id before dropping it
    # crashes on real prod data with pre-existing private memories.
    op.execute(
        sa.text(
            "ALTER TABLE memories DROP CONSTRAINT IF EXISTS memories_visibility_team_consistency"
        )
    )
    # Find and drop any CHECK constraint on visibility (it was inline, not named explicitly in 0015)
    op.execute(
        sa.text("""
        DO $$
        DECLARE
            cn TEXT;
        BEGIN
            FOR cn IN
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'memories'::regclass
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) ILIKE '%visibility%'
            LOOP
                EXECUTE 'ALTER TABLE memories DROP CONSTRAINT ' || quote_ident(cn);
            END LOOP;
        END$$;
    """)
    )

    # Backfill team_id for memories that don't have one. Safe now that the
    # old constraint is gone — private memories can transiently coexist with
    # a non-NULL team_id until the visibility rewrite below.
    op.execute(
        sa.text("""
        UPDATE memories m
        SET team_id = (
            SELECT t.id FROM teams t
            WHERE t.created_by_tenant_id = m.tenant_id
              AND t.team_type = 'personal'
              AND t.deleted_at IS NULL
            ORDER BY t.created_at ASC LIMIT 1
        )
        WHERE m.team_id IS NULL
          AND m.deleted_at IS NULL
    """)
    )

    # Rewrite 'private' → 'team' (every row by now has team_id set).
    op.execute(sa.text("UPDATE memories SET visibility = 'team' WHERE visibility = 'private'"))

    # Add the new CHECK (team / external / public). Default 'team'.
    op.execute(
        sa.text("""
        ALTER TABLE memories
        ADD CONSTRAINT memories_visibility_check
        CHECK (visibility IN ('team', 'external', 'public'))
    """)
    )

    # Default for new rows
    op.execute(sa.text("ALTER TABLE memories ALTER COLUMN visibility SET DEFAULT 'team'"))

    # =========================================================
    # 3. fragment_id column for thread-reply addressing
    # =========================================================
    op.execute(sa.text("ALTER TABLE memories ADD COLUMN fragment_id INT NULL"))
    op.execute(
        sa.text(
            "CREATE INDEX memories_parent_fragment_idx ON memories(parent_id, fragment_id) "
            "WHERE parent_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    # NOTE: this downgrade does NOT delete the auto-created personal teams
    # (they're real teams with their own data). It only reverses the
    # column / CHECK changes.
    op.execute(sa.text("DROP INDEX IF EXISTS memories_parent_fragment_idx"))
    op.execute(sa.text("ALTER TABLE memories DROP COLUMN IF EXISTS fragment_id"))
    op.execute(sa.text("ALTER TABLE memories DROP CONSTRAINT IF EXISTS memories_visibility_check"))
    op.execute(sa.text("ALTER TABLE memories ALTER COLUMN visibility SET DEFAULT 'private'"))
    op.execute(
        sa.text(
            "ALTER TABLE memories "
            "ADD CONSTRAINT memories_visibility_team_consistency "
            "CHECK ((visibility = 'private' AND team_id IS NULL) "
            "    OR (visibility = 'team' AND team_id IS NOT NULL))"
        )
    )
