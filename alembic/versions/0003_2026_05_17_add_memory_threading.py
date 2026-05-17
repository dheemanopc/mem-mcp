"""Add parent_id column to memories table for native threading support."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_add_memory_threading"
down_revision = "0002_seed_allowed_software"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add parent_id column and index for memory threading."""
    op.add_column(
        "memories",
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.execute(
        "CREATE INDEX memories_parent_id_idx ON memories(parent_id) WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    """Remove parent_id column and index."""
    op.execute("DROP INDEX IF EXISTS memories_parent_id_idx")
    op.drop_column("memories", "parent_id")
