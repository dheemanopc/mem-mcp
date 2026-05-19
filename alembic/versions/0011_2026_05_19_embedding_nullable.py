"""Allow memories.embedding to be NULL for oversize content.

Revision ID: 0011_embedding_nullable
Revises: 0010_memory_clusters
Create Date: 2026-05-19

Memory content can now be up to 200K characters. Bedrock Titan embeddings
cap at ~8K tokens (~32K chars). For oversize content we skip the embed
call entirely and store NULL; semantic search drops those rows (they
still surface via keyword scoring). See write.py / update.py for the
EMBED_MAX_INPUT_CHARS guard and hybrid_query.py for the IS NOT NULL
filter on the semantic CTE.
"""

import sqlalchemy as sa

from alembic import op

revision = "0011_embedding_nullable"
down_revision = "0010_memory_clusters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE memories ALTER COLUMN embedding DROP NOT NULL"))


def downgrade() -> None:
    # Reverting requires backfilling NULLs with a placeholder vector first;
    # leave that to the operator since the choice of placeholder is policy.
    op.execute(sa.text("ALTER TABLE memories ALTER COLUMN embedding SET NOT NULL"))
