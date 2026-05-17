"""Add tenant_skill_credentials table for the skill framework credential vault."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_add_skill_credentials"
down_revision = "0003_add_memory_threading"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add tenant_skill_credentials table with RLS and grants."""
    # Table
    op.execute(
        sa.text("""
        CREATE TABLE tenant_skill_credentials (
            tenant_id              UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            skill_name             TEXT NOT NULL CHECK (skill_name ~ '^[a-z][a-z0-9_]{0,31}$'),
            credentials_ciphertext BYTEA NOT NULL,
            schema_version         INT NOT NULL DEFAULT 1,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_refreshed_at      TIMESTAMPTZ,
            PRIMARY KEY (tenant_id, skill_name)
        )
        """)
    )

    # RLS
    op.execute(
        sa.text("""
        ALTER TABLE tenant_skill_credentials ENABLE ROW LEVEL SECURITY
        """)
    )
    op.execute(
        sa.text("""
        ALTER TABLE tenant_skill_credentials FORCE ROW LEVEL SECURITY
        """)
    )

    # Policy
    op.execute(
        sa.text("""
        CREATE POLICY tenant_skill_credentials_tenant_isolation ON tenant_skill_credentials
            USING      (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """)
    )

    # Index
    op.execute(
        sa.text("""
        CREATE INDEX idx_tenant_skill_credentials_skill
            ON tenant_skill_credentials(skill_name)
        """)
    )

    # Grants
    op.execute(
        sa.text("""
        GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_skill_credentials TO mem_app
        """)
    )


def downgrade() -> None:
    """Remove tenant_skill_credentials table."""
    op.execute(sa.text("DROP INDEX IF EXISTS idx_tenant_skill_credentials_skill"))
    op.execute(sa.text("DROP TABLE IF EXISTS tenant_skill_credentials"))
