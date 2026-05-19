"""Add TTL (expires_at) and embed opt-out (indexable) flags to memories.

Revision ID: 0013_transient_and_indexable
Revises: 0012_tenant_storage_bytes
Create Date: 2026-05-19

Adds two new columns on memories:

  expires_at  — optional timestamp when a transient memory should be
                soft-deleted. Used for agent-to-agent ephemeral state,
                session metadata, and other short-lived content. NULL
                means the memory lives indefinitely (default behavior).
                Soft-deletion happens via the daily expire_transient job.

  indexable   — boolean flag to opt out of Bedrock embedding. Useful for
                batched ingest where semantic search is not needed. Reduces
                embed latency + cost, while keeping the memory stored and
                searchable by tags, metadata, and text content (non-semantic).
                Defaults to TRUE (embed by default).

Index on expires_at for efficient cleanup: scans only non-null, non-deleted rows.
"""

import sqlalchemy as sa

from alembic import op

revision = "0013_transient_and_indexable"
down_revision = "0012_tenant_storage_bytes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE memories "
            "ADD COLUMN expires_at TIMESTAMPTZ NULL, "
            "ADD COLUMN indexable BOOLEAN NOT NULL DEFAULT TRUE"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX idx_memories_expires_at_active "
            "ON memories(expires_at) "
            "WHERE expires_at IS NOT NULL AND deleted_at IS NULL"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_memories_expires_at_active"))
    op.execute(
        sa.text(
            "ALTER TABLE memories "
            "DROP COLUMN IF EXISTS indexable, "
            "DROP COLUMN IF EXISTS expires_at"
        )
    )
