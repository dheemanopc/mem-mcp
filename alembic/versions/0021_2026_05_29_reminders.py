"""reminder_items and reminder_events tables for the reminders plugin.

Revision ID: 0021_reminders
Revises: 0020_plugin_notices
Create Date: 2026-05-29
"""

import sqlalchemy as sa

from alembic import op

revision = "0021_reminders"
down_revision = "0020_plugin_notices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # reminder_items: stores reminder definitions and state
    op.create_table(
        "reminder_items",
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
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("fire_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("cadence", sa.Text(), nullable=True),
        sa.Column(
            "state",
            sa.Text(),
            nullable=False,
            server_default="active",
        ),
        sa.Column("snooze_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("dismissed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_fired_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("fire_count", sa.Integer(), nullable=False, server_default="0"),
    )

    # reminder_events: audit log of state transitions
    op.create_table(
        "reminder_events",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "item_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reminder_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=True),
    )

    # Indexes
    op.create_index(
        "idx_reminder_items_due",
        "reminder_items",
        ["tenant_id", "fire_at"],
        postgresql_where=sa.text(
            "state='active' AND (snooze_until IS NULL OR snooze_until < now())"
        ),
    )
    op.create_index(
        "idx_reminder_events_item",
        "reminder_events",
        ["item_id", "occurred_at"],
        postgresql_desc={"occurred_at": True},
    )


def downgrade() -> None:
    op.drop_index("idx_reminder_events_item", table_name="reminder_events")
    op.drop_index("idx_reminder_items_due", table_name="reminder_items")
    op.drop_table("reminder_events")
    op.drop_table("reminder_items")
