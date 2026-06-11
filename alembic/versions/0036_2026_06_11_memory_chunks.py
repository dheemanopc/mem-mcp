"""memory_chunks table for chunk-level vector retrieval (memory_search_chunks).

Per the snippet-search proposal (memsys memo 3e66b43c): long memories force
whole-document fetches into the caller's context; chunk-level embeddings let
a semantic match localize to the passage that actually matched.

Design:
- Chunks are built ASYNCHRONOUSLY by the chunk_build job (no write-latency
  impact); source_content_hash records the memories.content_hash the chunks
  were built from, so content updates mark chunks stale for rebuild.
- ON DELETE CASCADE from memories handles hard deletes; soft-delete /
  is_current / indexable filtering happens at query time via the JOIN to
  memories (the search SQL must always join).
- RLS uses the NULLIF guard pattern (migrations 0031/0033 lesson): bare
  ::uuid casts of the GUC crash on recycled pool connections where the GUC
  is '' instead of NULL.
- The chunk_build job runs on the maint role (table owner) and bypasses
  RLS for inserts; mem_app only ever SELECTs.

Revision ID: 0036_memory_chunks
Revises: 0035_can_access_team_resource
Create Date: 2026-06-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0036_memory_chunks"
down_revision = "0035_can_access_team_resource"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        CREATE TABLE memory_chunks (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            memory_id            UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            chunk_index          INT NOT NULL,
            content              TEXT NOT NULL,
            embedding            vector(1024),
            source_content_hash  TEXT NOT NULL,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (memory_id, chunk_index)
        )
    """)
    )

    # Semantic probe path (same index family as memories.embedding).
    op.execute(
        sa.text("""
        CREATE INDEX memory_chunks_embedding_idx
            ON memory_chunks USING hnsw (embedding vector_cosine_ops)
    """)
    )

    # Rebuild/staleness checks + cascade-adjacent lookups.
    op.execute(
        sa.text("""
        CREATE INDEX memory_chunks_memory_idx
            ON memory_chunks (memory_id)
    """)
    )

    op.execute(sa.text("ALTER TABLE memory_chunks ENABLE ROW LEVEL SECURITY"))
    op.execute(
        sa.text("""
        CREATE POLICY memory_chunks_select ON memory_chunks
            FOR SELECT TO mem_app
            USING (
                tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
            )
    """)
    )

    op.execute(sa.text("GRANT SELECT ON memory_chunks TO mem_app"))


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS memory_chunks_select ON memory_chunks"))
    op.execute(sa.text("DROP TABLE IF EXISTS memory_chunks"))
