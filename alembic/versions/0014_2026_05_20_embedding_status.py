"""Add embedding_status column to memories.

Revision ID: 0014_embedding_status
Revises: 0013_transient_and_indexable
Create Date: 2026-05-20

Adds embedding_status column to track the lifecycle of Bedrock embedding attempts.
Possible values:
  - ok                      — embedding succeeded and stored
  - skipped_oversize        — content > 32KB, not embedded per safety limit
  - skipped_opt_out         — indexable=false, skipped by user request
  - failed_throttled        — Bedrock returned throttle (429); retryable
  - failed_unavailable      — Bedrock unavailable (503+); retryable
  - failed_validation       — Bad input (malformed UTF-8, encoding issues); not retryable
  - failed_unknown          — Other error; retryable

Backfill existing rows using best-effort heuristics:
  - If embedding IS NOT NULL → 'ok'
  - If indexable = false → 'skipped_opt_out'
  - If indexable = true AND content length > 32000 → 'skipped_oversize'
  - Otherwise (most common for existing NULL embeddings) → 'failed_unknown'

Creates a partial index on candidates for the embedding backfill worker to retry.
"""

import sqlalchemy as sa

from alembic import op

revision = "0014_embedding_status"
down_revision = "0013_transient_and_indexable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE memories "
            "ADD COLUMN embedding_status TEXT NOT NULL DEFAULT 'failed_unknown' "
            "CHECK (embedding_status IN ("
            "  'ok', "
            "  'skipped_oversize', "
            "  'skipped_opt_out', "
            "  'failed_throttled', "
            "  'failed_unavailable', "
            "  'failed_validation', "
            "  'failed_unknown'"
            "))"
        )
    )

    op.execute(
        sa.text("UPDATE memories SET embedding_status = 'ok' " "WHERE embedding IS NOT NULL")
    )

    op.execute(
        sa.text(
            "UPDATE memories SET embedding_status = 'skipped_opt_out' "
            "WHERE embedding IS NULL AND indexable = false"
        )
    )

    op.execute(
        sa.text(
            "UPDATE memories SET embedding_status = 'skipped_oversize' "
            "WHERE embedding IS NULL AND indexable = true AND length(content) > 32000"
        )
    )

    op.execute(
        sa.text(
            "CREATE INDEX idx_memories_embedding_backfill_candidates "
            "ON memories(created_at) "
            "WHERE embedding IS NULL "
            "  AND embedding_status IN ('failed_throttled', 'failed_unavailable', 'failed_unknown') "
            "  AND deleted_at IS NULL"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_memories_embedding_backfill_candidates"))
    op.execute(sa.text("ALTER TABLE memories DROP COLUMN IF EXISTS embedding_status"))
