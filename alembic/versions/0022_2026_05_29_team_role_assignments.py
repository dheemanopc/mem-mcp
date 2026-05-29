"""team_role_assignments — DAG-aware membership (users + teams-as-members).

Polymorphic FK enforced by trigger (PG doesn't support polymorphic FKs natively).
Per foundation 1c47bd99 + amendments 99eacba4 + 6d3b4bfe.
Amendment #2: DAG-walk concurrency uses transaction-scoped ADVISORY LOCK
in the application layer (src/mem_mcp/teams/dag.py); this migration only
provides the schema. teams.team_type + teams.parent_count added.

Revision ID: 0022_team_role_assignments
Revises: 0021_roles_catalog
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0022_team_role_assignments"
down_revision = "0021_roles_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # New table
    op.execute(
        sa.text("""
        CREATE TABLE team_role_assignments (
            parent_team_id      UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            member_kind         TEXT NOT NULL CHECK (member_kind IN ('user', 'team')),
            member_id           UUID NOT NULL,
            role_id             UUID NOT NULL REFERENCES roles_catalog(id) ON DELETE RESTRICT,
            status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','pending','revoked')),
            assigned_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            assigned_by_user_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            invited_email       TEXT NULL,
            PRIMARY KEY (parent_team_id, member_kind, member_id)
        )
    """)
    )
    op.execute(
        sa.text(
            "CREATE INDEX tra_member_idx ON team_role_assignments(member_kind, member_id) WHERE status = 'active'"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX tra_parent_idx ON team_role_assignments(parent_team_id) WHERE status = 'active'"
        )
    )

    # Polymorphic FK trigger
    op.execute(
        sa.text("""
        CREATE OR REPLACE FUNCTION enforce_team_role_assignment_member_fk()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.member_kind = 'user' THEN
                IF NOT EXISTS (SELECT 1 FROM tenants WHERE id = NEW.member_id) THEN
                    RAISE EXCEPTION 'member_id % does not exist in tenants (member_kind=user)', NEW.member_id
                        USING ERRCODE = 'foreign_key_violation';
                END IF;
            ELSIF NEW.member_kind = 'team' THEN
                IF NOT EXISTS (SELECT 1 FROM teams WHERE id = NEW.member_id) THEN
                    RAISE EXCEPTION 'member_id % does not exist in teams (member_kind=team)', NEW.member_id
                        USING ERRCODE = 'foreign_key_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER team_role_assignments_member_fk_trg
        BEFORE INSERT OR UPDATE ON team_role_assignments
        FOR EACH ROW EXECUTE FUNCTION enforce_team_role_assignment_member_fk()
    """)
    )

    # ALTER teams: add team_type + parent_count
    op.execute(
        sa.text("""
        ALTER TABLE teams
            ADD COLUMN team_type TEXT NOT NULL DEFAULT 'general'
                CHECK (team_type IN ('personal','workspace','project','dept','customer','org','general')),
            ADD COLUMN parent_count INT NOT NULL DEFAULT 0
    """)
    )

    # Trigger: maintain teams.parent_count
    op.execute(
        sa.text("""
        CREATE OR REPLACE FUNCTION maintain_team_parent_count()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' AND NEW.member_kind = 'team' THEN
                UPDATE teams SET parent_count = parent_count + 1 WHERE id = NEW.member_id;
            ELSIF TG_OP = 'DELETE' AND OLD.member_kind = 'team' THEN
                UPDATE teams SET parent_count = parent_count - 1 WHERE id = OLD.member_id;
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$;
    """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER team_role_assignments_parent_count_trg
        AFTER INSERT OR DELETE ON team_role_assignments
        FOR EACH ROW EXECUTE FUNCTION maintain_team_parent_count()
    """)
    )

    # Backfill from existing team_members
    op.execute(
        sa.text("""
        INSERT INTO team_role_assignments
            (parent_team_id, member_kind, member_id, role_id, status, assigned_at, assigned_by_user_id, invited_email)
        SELECT
            tm.team_id,
            'user',
            tm.tenant_id,
            (SELECT id FROM roles_catalog WHERE role_key = tm.role AND plugin_id IS NULL),
            tm.status,
            tm.added_at,
            tm.added_by_tenant_id,
            tm.invited_email
        FROM team_members tm
        ON CONFLICT (parent_team_id, member_kind, member_id) DO NOTHING
    """)
    )

    op.execute(sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON team_role_assignments TO mem_app"))


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS team_role_assignments_parent_count_trg ON team_role_assignments"
        )
    )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS team_role_assignments_member_fk_trg ON team_role_assignments"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS maintain_team_parent_count() CASCADE"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS enforce_team_role_assignment_member_fk() CASCADE"))
    op.execute(sa.text("ALTER TABLE teams DROP COLUMN IF EXISTS parent_count"))
    op.execute(sa.text("ALTER TABLE teams DROP COLUMN IF EXISTS team_type"))
    op.execute(sa.text("DROP TABLE IF EXISTS team_role_assignments"))
