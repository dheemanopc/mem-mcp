"""Add memory_clusters table for precomputed near-duplicate clusters.

Revision ID: 0010_memory_clusters
Revises: 0009_memory_access_tracking
Create Date: 2026-05-19

Phase-2 memory management: the cluster_build job runs weekly, finds groups
of memories where pairwise cosine_sim > threshold (BFS over the
edge graph), and writes each group as a row here. The /memories "Similar"
tab reads precomputed clusters and renders a bulk-cleanup UI.

member_ids: array of UUIDs that participate in the cluster. Tiny in
practice (most clusters are 2-5 memories), array is fine over a join table.
"""

import sqlalchemy as sa

from alembic import op

revision = "0010_memory_clusters"
down_revision = "0009_memory_access_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE memory_clusters (
                id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                member_ids            UUID[] NOT NULL,
                member_count          INT NOT NULL,
                similarity_threshold  REAL NOT NULL,
                seed_memory_id        UUID NOT NULL,
                built_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
                CHECK (member_count >= 2),
                CHECK (array_length(member_ids, 1) = member_count)
            )
            """
        )
    )
    op.execute(sa.text("CREATE INDEX idx_memory_clusters_tenant ON memory_clusters(tenant_id)"))

    # RLS — same pattern as kite_intents (0007).
    op.execute(sa.text("ALTER TABLE memory_clusters ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE memory_clusters FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation_select ON memory_clusters
                FOR SELECT
                USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation_insert ON memory_clusters
                FOR INSERT
                WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation_delete ON memory_clusters
                FOR DELETE
                USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
            """
        )
    )

    op.execute(sa.text("GRANT SELECT, INSERT, DELETE ON memory_clusters TO mem_app"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS memory_clusters CASCADE"))
