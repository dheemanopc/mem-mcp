"""Mind-map branch status + cold-reload criteria — the context-economy substrate.

Maps are walked turn-by-turn inside one session today, so every earlier branch's
full discussion stays resident in context while later branches are worked. Cost
grows with total discussion volume rather than with open work.

Two things were missing to fix that:

1. **Branch status.** ``mindmap_close`` closes the whole map; there was no
   per-branch equivalent, and ``memory_map_membership`` had nowhere to record
   that a branch is settled. ``open_loops`` was a naive filter on
   ``metadata.responsible_party == 'owner'`` that never checked whether the loop
   had been answered, so it grew monotonically and included owner-authored notes
   that were never questions at all.

   Status is stored, NOT derived from ``reference_kind``. That column is
   unvalidated free text (max_length=128), and the read path deciding what enters
   the agent's context must not depend on string-matching a field nothing
   enforces — one typo ("resolved-under") would silently reopen a settled branch
   and re-inflate context, with no error anywhere.

2. **Decision criteria.** Batch-ask/cold-reload only works if a question carries
   what to do with each answer. Written at ask time (when the reasoning is
   already in context, so it is free), read at reload time (when it is not).

``turn`` is promoted out of ``memories.metadata.responsible_party`` into a real
column so the open-loop query is an indexed scan rather than a JSONB probe.

``memory_map_events.id`` is already a BIGSERIAL on an append-only log, so it
serves as the delta cursor with no new sequence or column.

Revision ID: 0044_mindmap_branch_status
Revises: 0043_backfill_personal_teams
Create Date: 2026-08-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0044_mindmap_branch_status"
down_revision = "0043_backfill_personal_teams"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        ALTER TABLE memory_map_membership
            ADD COLUMN status TEXT NOT NULL DEFAULT 'open',
            ADD COLUMN turn TEXT NULL,
            ADD COLUMN resolution_summary TEXT NULL,
            ADD COLUMN resolved_by_memory_id UUID NULL REFERENCES memories(id) ON DELETE SET NULL,
            ADD COLUMN resolved_by_party TEXT NULL,
            ADD COLUMN resolved_at TIMESTAMPTZ NULL,
            ADD COLUMN decision_criteria TEXT NULL
    """)
    )
    op.execute(
        sa.text("""
        ALTER TABLE memory_map_membership
            ADD CONSTRAINT memory_map_membership_status_check
                CHECK (status IN ('open', 'resolved', 'dropped')),
            ADD CONSTRAINT memory_map_membership_turn_check
                CHECK (turn IS NULL OR turn IN ('owner', 'model')),
            ADD CONSTRAINT memory_map_membership_resolved_by_party_check
                CHECK (resolved_by_party IS NULL OR resolved_by_party IN ('owner', 'model'))
    """)
    )

    # Partial index: the hot query is "open branches of this map", and the whole
    # point of the feature is that this stays cheap as maps grow.
    op.execute(
        sa.text("""
        CREATE INDEX memory_map_membership_open_idx
            ON memory_map_membership (root_memory_id, turn)
            WHERE status = 'open'
    """)
    )

    # Backfill turn from the metadata that already carries it.
    op.execute(
        sa.text("""
        UPDATE memory_map_membership mm
        SET turn = m.metadata->>'responsible_party'
        FROM memories m
        WHERE m.id = mm.memory_id
          AND m.metadata->>'responsible_party' IN ('owner', 'model')
    """)
    )

    # Graduation guard: when an owner writes after the agent's last read,
    # mindmap_close must refuse rather than graduate past unread human input.
    op.execute(
        sa.text("""
        ALTER TABLE memory_maps
            ADD COLUMN last_owner_write_at TIMESTAMPTZ NULL,
            ADD COLUMN last_agent_read_at TIMESTAMPTZ NULL
    """)
    )


def downgrade() -> None:
    op.execute(
        sa.text("""
        ALTER TABLE memory_maps
            DROP COLUMN IF EXISTS last_agent_read_at,
            DROP COLUMN IF EXISTS last_owner_write_at
    """)
    )
    op.execute(sa.text("DROP INDEX IF EXISTS memory_map_membership_open_idx"))
    op.execute(
        sa.text("""
        ALTER TABLE memory_map_membership
            DROP CONSTRAINT IF EXISTS memory_map_membership_resolved_by_party_check,
            DROP CONSTRAINT IF EXISTS memory_map_membership_turn_check,
            DROP CONSTRAINT IF EXISTS memory_map_membership_status_check
    """)
    )
    op.execute(
        sa.text("""
        ALTER TABLE memory_map_membership
            DROP COLUMN IF EXISTS decision_criteria,
            DROP COLUMN IF EXISTS resolved_at,
            DROP COLUMN IF EXISTS resolved_by_party,
            DROP COLUMN IF EXISTS resolved_by_memory_id,
            DROP COLUMN IF EXISTS resolution_summary,
            DROP COLUMN IF EXISTS turn,
            DROP COLUMN IF EXISTS status
    """)
    )
