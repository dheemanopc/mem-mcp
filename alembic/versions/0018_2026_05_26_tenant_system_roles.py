"""tenant_system_roles + system_state for bootstrap idempotency.

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-26

Adds:
1. tenant_system_roles: tracks system-level role grants (system_admin, system_support)
2. system_state: singleton key/value store for bootstrap markers and system metadata
"""

import sqlalchemy as sa

from alembic import op

revision = "0018_tenant_system_roles"
down_revision = "0017_team_invites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply tenant_system_roles and system_state schema."""

    # ==========================================================================
    # NEW TABLE: tenant_system_roles
    # ==========================================================================
    op.create_table(
        "tenant_system_roles",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "granted_by_tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "granted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "tenant_id",
            "role",
            name="uq_tenant_system_roles_tenant_role",
        ),
        sa.CheckConstraint(
            "role IN ('system_admin', 'system_support')",
            name="ck_tenant_system_roles_role",
        ),
    )
    op.create_index(
        "idx_tenant_system_roles_tenant",
        "tenant_system_roles",
        ["tenant_id"],
    )

    # ==========================================================================
    # GRANTS to mem_app role
    # ==========================================================================
    op.execute(
        sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_system_roles TO mem_app")
    )

    # ==========================================================================
    # NEW TABLE: system_state
    # ==========================================================================
    op.create_table(
        "system_state",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column(
            "value",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ==========================================================================
    # GRANTS to mem_app role for system_state
    # ==========================================================================
    op.execute(
        sa.text("GRANT SELECT, INSERT, UPDATE ON system_state TO mem_app")
    )


def downgrade() -> None:
    """Revert tenant_system_roles and system_state schema."""

    # Drop tenant_system_roles
    op.drop_index("idx_tenant_system_roles_tenant", table_name="tenant_system_roles")
    op.drop_table("tenant_system_roles")

    # Drop system_state
    op.drop_table("system_state")
