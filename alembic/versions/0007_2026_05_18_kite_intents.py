"""Add kite_intents table for S2S credential handoff + referrer to tenants."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_kite_intents"
down_revision = "0006_dcr_two_tier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add referrer column to tenants and create kite_intents table with RLS."""
    # Add referrer column to tenants
    op.execute(sa.text("ALTER TABLE tenants " "ADD COLUMN referrer TEXT"))

    # Create kite_intents table
    op.execute(
        sa.text("""
        CREATE TABLE kite_intents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            ciphertext BYTEA NOT NULL,
            encryption_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by_client_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ,
            consumed_outcome TEXT
        )
        """)
    )

    # Create indexes
    op.execute(
        sa.text("""
        CREATE INDEX idx_kite_intents_tenant_id ON kite_intents(tenant_id)
        """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX idx_kite_intents_pending ON kite_intents(expires_at)
        WHERE consumed_at IS NULL
        """)
    )

    # Enable RLS
    op.execute(
        sa.text("""
        ALTER TABLE kite_intents ENABLE ROW LEVEL SECURITY
        """)
    )
    op.execute(
        sa.text("""
        ALTER TABLE kite_intents FORCE ROW LEVEL SECURITY
        """)
    )

    # Create RLS policies
    op.execute(
        sa.text("""
        CREATE POLICY tenant_isolation_select ON kite_intents
        FOR SELECT
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """)
    )
    op.execute(
        sa.text("""
        CREATE POLICY tenant_isolation_insert ON kite_intents
        FOR INSERT
        WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """)
    )
    op.execute(
        sa.text("""
        CREATE POLICY tenant_isolation_update ON kite_intents
        FOR UPDATE
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """)
    )
    op.execute(
        sa.text("""
        CREATE POLICY tenant_isolation_delete ON kite_intents
        FOR DELETE
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """)
    )

    # Grant permissions
    op.execute(
        sa.text("""
        GRANT SELECT, INSERT, UPDATE, DELETE ON kite_intents TO mem_app
        """)
    )


def downgrade() -> None:
    """Remove kite_intents table and referrer column."""
    # Drop policies
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_delete ON kite_intents"))
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_update ON kite_intents"))
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_insert ON kite_intents"))
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_select ON kite_intents"))

    # Drop indexes
    op.execute(sa.text("DROP INDEX IF EXISTS idx_kite_intents_pending"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_kite_intents_tenant_id"))

    # Drop table
    op.execute(sa.text("DROP TABLE IF EXISTS kite_intents"))

    # Drop referrer column
    op.execute(sa.text("ALTER TABLE tenants DROP COLUMN IF EXISTS referrer"))
