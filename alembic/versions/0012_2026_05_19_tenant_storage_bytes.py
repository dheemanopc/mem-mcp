"""Per-tenant total_storage_bytes tracking.

Revision ID: 0012_tenant_storage_bytes
Revises: 0011_embedding_nullable
Create Date: 2026-05-19

Adds two columns on tenants for tracking aggregate memory storage usage:

  total_storage_bytes        — sum of length(content) across the tenant's
                               current, non-deleted memories. Raw text bytes,
                               not on-disk bytes (TOAST compression typically
                               cuts actual disk usage 30-50%). Used as an
                               upper-bound quota signal.

  storage_bytes_updated_at   — when the value was last recomputed. NULL until
                               the daily storage_stats job runs the first time.

Refresh is via the storage_stats job (mem_mcp.jobs.storage_stats) on a daily
systemd timer. Choose periodic over per-write trigger: write latency stays
clean and accidentally-quadratic re-computes are avoided. Trade-off: the
number lags reality by up to 24 hours, which is fine for a "how much have
I stored" indicator.
"""

import sqlalchemy as sa

from alembic import op

revision = "0012_tenant_storage_bytes"
down_revision = "0011_embedding_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE tenants "
            "ADD COLUMN total_storage_bytes BIGINT NOT NULL DEFAULT 0, "
            "ADD COLUMN storage_bytes_updated_at TIMESTAMPTZ"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE tenants "
            "DROP COLUMN IF EXISTS storage_bytes_updated_at, "
            "DROP COLUMN IF EXISTS total_storage_bytes"
        )
    )
