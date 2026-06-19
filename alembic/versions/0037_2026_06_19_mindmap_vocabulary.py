"""mindmap vocabulary — add thinking-node types + the `map` slug resource.

Per mind-map build design (docs/mindmap-v1-build-design.md) + ADR-0007/0008:

- `memories.type` gains `position` and `challenge` — first-class thinking
  nodes captured inside a map. Both are WORKING nodes: non-versioned,
  edit-in-place (they join note/snippet/question; only decision/fact stay
  versioned, per ADR-0005).
- `slugs.resource_type` gains `map` — a mind-map is rooted at a memory and
  addressed by that root memory's slug (the memsys "key"). Owner decision:
  the map's identity IS a memsys key, not an opaque registry id.

Purely additive constraint widening; no rows are rewritten.

Revision ID: 0037_mindmap_vocabulary
Revises: 0036_memory_chunks
Create Date: 2026-06-19
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0037_mindmap_vocabulary"
down_revision = "0036_memory_chunks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Widen memories.type to include the two thinking-node types.
    op.execute(sa.text("ALTER TABLE memories DROP CONSTRAINT IF EXISTS memories_type_check"))
    op.execute(
        sa.text("""
        ALTER TABLE memories ADD CONSTRAINT memories_type_check
            CHECK (type IN ('note','decision','fact','snippet','question','position','challenge'))
        """)
    )

    # Widen slugs.resource_type to allow a map root to be minted a slug.
    op.execute(sa.text("ALTER TABLE slugs DROP CONSTRAINT IF EXISTS slugs_resource_type_check"))
    op.execute(
        sa.text("""
        ALTER TABLE slugs ADD CONSTRAINT slugs_resource_type_check
            CHECK (resource_type IN ('decision','fact','map'))
        """)
    )


def downgrade() -> None:
    # Reverting fails if any position/challenge memories or map slugs exist.
    # That is intentional — the constraint cannot narrow past live data.
    op.execute(sa.text("ALTER TABLE slugs DROP CONSTRAINT IF EXISTS slugs_resource_type_check"))
    op.execute(
        sa.text("""
        ALTER TABLE slugs ADD CONSTRAINT slugs_resource_type_check
            CHECK (resource_type IN ('decision','fact'))
        """)
    )

    op.execute(sa.text("ALTER TABLE memories DROP CONSTRAINT IF EXISTS memories_type_check"))
    op.execute(
        sa.text("""
        ALTER TABLE memories ADD CONSTRAINT memories_type_check
            CHECK (type IN ('note','decision','fact','snippet','question'))
        """)
    )
