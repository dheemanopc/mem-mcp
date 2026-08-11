"""Repair the 0044 `turn` backfill — metadata is double-encoded JSONB.

0044 backfilled ``memory_map_membership.turn`` with::

    m.metadata->>'responsible_party'

which matched **nothing**. ``memories.metadata`` is a ``jsonb`` column holding a
JSON-encoded *string*, not a JSON object — ``jsonb_typeof`` returns ``'string'``
for every row::

    "{\\"node_role\\": \\"position\\", \\"responsible_party\\": \\"owner\\", ...}"

You cannot key into a JSON string scalar, so ``->>`` yields NULL and the UPDATE
touched 0 of 208 rows on prod while reporting success. The application never
noticed because ``mindmap.service._as_dict`` json.loads the string, so Python
sees a dict; only SQL sees the double encoding.

Consequence: every node created before 0044 had ``turn = NULL``, so
``open_loops`` was empty for every pre-existing map. Nodes created after 0044 are
unaffected — ``insert_membership`` sets ``turn`` from an explicit parameter and
never reads metadata.

This migration re-runs the backfill decoding both shapes, so it is correct now
and stays correct if the write path is ever fixed to store real objects.

Verified against prod data before writing: the decode resolves 96 'owner' and
67 'model' rows that 0044 left NULL.

NOTE (not fixed here): the double encoding itself is a broader latent issue —
ANY SQL that queries a metadata key silently returns NULL rather than erroring.
Worth auditing separately; changing the write path needs a data migration across
all memories, which is far wider than this repair.

Revision ID: 0045_fix_turn_backfill
Revises: 0044_mindmap_branch_status
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0045_fix_turn_backfill"
down_revision = "0044_mindmap_branch_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        UPDATE memory_map_membership mm
        SET turn = decoded.rp
        FROM (
            SELECT
                m.id AS memory_id,
                CASE
                    WHEN jsonb_typeof(m.metadata) = 'string'
                        THEN (m.metadata #>> ARRAY[]::text[])::jsonb ->> 'responsible_party'
                    WHEN jsonb_typeof(m.metadata) = 'object'
                        THEN m.metadata ->> 'responsible_party'
                    ELSE NULL
                END AS rp
            FROM memories m
        ) AS decoded
        WHERE decoded.memory_id = mm.memory_id
          AND mm.turn IS NULL
          AND decoded.rp IN ('owner', 'model')
    """)
    )


def downgrade() -> None:
    # Deliberately a no-op. Clearing `turn` would be indistinguishable from the
    # broken state 0044 left behind, and there is no record of which rows this
    # migration set versus which were written correctly at insert time.
    pass
