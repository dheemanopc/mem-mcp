"""plugin_notices table for cross-plugin async notice delivery.

Revision ID: 0020_plugin_notices
Revises: 0019_nullif_empty_tenant
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_plugin_notices"
down_revision = "0019_nullif_empty_tenant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plugin_notices",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plugin_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_plugin_notices_pending",
        "plugin_notices",
        ["tenant_id", "expires_at"],
        postgresql_where=sa.text("delivered_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_plugin_notices_pending", table_name="plugin_notices")
    op.drop_table("plugin_notices")
