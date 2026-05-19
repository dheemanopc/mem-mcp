"""Add last_accessed_at + access_count to memories for stale-detection.

Revision ID: 0009_memory_access_tracking
Revises: 0008_algotrade_localhost
Create Date: 2026-05-19

Phase-1 memory management feature: lets the user (via /memories UI) find
memories that haven't been read in a long time and clean them up.

Notably NO index on last_accessed_at:

- Per-tenant memory volume is small (≤ 10k expected). Sequential scan with
  tenant_id RLS filter is ~10ms at that scale, well below UI latency budget.
- Indexing would kill Postgres HOT (Heap-Only Tuple) updates, since every
  memory_get and memory_search bumps last_accessed_at. HOT-friendly updates
  keep bloat bounded for what is effectively a write-heavy column.

If we hit > 50k memories per tenant later, add a partial index:
    CREATE INDEX idx_memories_stale ON memories(tenant_id, last_accessed_at)
      WHERE last_accessed_at < now() - interval '90 days';
"""

import sqlalchemy as sa

from alembic import op

revision = "0009_memory_access_tracking"
down_revision = "0008_algotrade_localhost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE memories "
            "ADD COLUMN last_accessed_at TIMESTAMPTZ, "
            "ADD COLUMN access_count INT NOT NULL DEFAULT 0"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE memories "
            "DROP COLUMN IF EXISTS access_count, "
            "DROP COLUMN IF EXISTS last_accessed_at"
        )
    )
