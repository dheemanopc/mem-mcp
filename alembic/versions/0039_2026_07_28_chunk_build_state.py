"""chunk_build_state — per-memory backoff/terminal state for the chunk-build job.

Fixes a runaway. chunk_build selected any memory lacking a current-hash chunk
set (no memory_chunks row with source_content_hash = memories.content_hash) and,
on an embed failure, wrote NO state and did NOT advance (ORDER BY created_at ASC,
LIMIT 50). So a handful of un-embeddable memories at the front of the queue were
re-chunked and re-embedded on every 5-minute pass forever — ~94k bedrock
InvokeModel/day, a self-sustaining Bedrock-throttle loop, and (via a duplicate
CloudTrail trail) the dominant line on the AWS bill. Meanwhile the backlog never
drained: only ~2% of memories ever got chunks, so memory_search_chunks was
effectively broken.

This table records attempts + a backoff deadline + a terminal flag so a memory
that cannot be chunk-embedded stops being re-selected: transient failures back
off exponentially; invalid_input (permanent) and >= MAX_ATTEMPTS terminalize.
On a successful build the row is deleted (see chunk_build._CLEAR_STATE_SQL).

- Owned by the maint role (chunk_build runs as maint, the table owner); mem_app
  never touches it, so no RLS / policies / grants — consistent with an internal
  ops-only table.
- ON DELETE CASCADE from memories cleans the row on hard delete.

Revision ID: 0039_chunk_build_state
Revises: 0038_mindmap_tables
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0039_chunk_build_state"
down_revision = "0038_mindmap_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        CREATE TABLE chunk_build_state (
            memory_id        UUID PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
            attempts         INT NOT NULL DEFAULT 0,
            next_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            terminal         BOOLEAN NOT NULL DEFAULT false,
            last_error       TEXT,
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    )
    # Ready-candidate probe: only non-terminal rows matter to the candidate
    # SELECT, ordered by when they become eligible again.
    op.execute(
        sa.text("""
        CREATE INDEX chunk_build_state_ready_idx
            ON chunk_build_state (next_attempt_at)
            WHERE NOT terminal
    """)
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS chunk_build_state"))
