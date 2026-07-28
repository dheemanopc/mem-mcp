"""mindmap tables — maps, exclusive node membership, observability events.

Per mind-map build design (docs/mindmap-v1-build-design.md). A map is a root
memory plus the nodes that hang off it; these tables hold the lifecycle state
that is NOT node content (node content lives in `memories`, edges in
`memory_references`).

- `memory_maps`            : one row per map, keyed by its root memory. Holds
                             live/archived state, the reviewer write-counter,
                             seed origin, and the graduated spec-index pointer.
- `memory_map_membership`  : exclusive node ownership. PRIMARY KEY (memory_id)
                             makes "a node belongs to at most one map" a
                             STRUCTURAL guarantee, not a convention. No row =
                             unmapped (facts, old flat memories) — untouched.
- `memory_map_events`      : append-only observability log (day one, per spec
                             node 0aa54585) — ratify / fork / promote /
                             split_flag / review_flag / open / close.

RLS follows the canonical `safe_current_tenant_id()` pattern (migration 0034).

Revision ID: 0038_mindmap_tables
Revises: 0037_mindmap_vocabulary
Create Date: 2026-06-19
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0038_mindmap_tables"
down_revision = "0037_mindmap_vocabulary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- memory_maps -------------------------------------------------------
    op.execute(
        sa.text("""
        CREATE TABLE memory_maps (
            root_memory_id        UUID PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
            tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            team_id               UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            title                 TEXT NOT NULL,
            state                 TEXT NOT NULL DEFAULT 'live'
                                  CHECK (state IN ('live','archived')),
            writes_since_review   INT  NOT NULL DEFAULT 0,
            review_threshold      INT  NOT NULL DEFAULT 15,
            seed_spec_memory_id   UUID REFERENCES memories(id) ON DELETE SET NULL,
            graduated_index_id    UUID REFERENCES memories(id) ON DELETE SET NULL,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            closed_at             TIMESTAMPTZ
        )
    """)
    )
    # Find live maps seeded from a given spec node (from_spec dedup guard).
    op.execute(
        sa.text(
            "CREATE INDEX memory_maps_seed_idx ON memory_maps(seed_spec_memory_id) "
            "WHERE seed_spec_memory_id IS NOT NULL"
        )
    )
    op.execute(
        sa.text("CREATE INDEX memory_maps_tenant_state_idx ON memory_maps(tenant_id, state)")
    )

    # ---- memory_map_membership --------------------------------------------
    # PK on memory_id == exclusive ownership: a node cannot be in two maps.
    op.execute(
        sa.text("""
        CREATE TABLE memory_map_membership (
            memory_id        UUID PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
            root_memory_id   UUID NOT NULL REFERENCES memory_maps(root_memory_id) ON DELETE CASCADE,
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            node_role        TEXT NOT NULL,
            added_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    )
    op.execute(
        sa.text(
            "CREATE INDEX memory_map_membership_root_idx "
            "ON memory_map_membership(root_memory_id)"
        )
    )

    # ---- memory_map_events -------------------------------------------------
    op.execute(
        sa.text("""
        CREATE TABLE memory_map_events (
            id               BIGSERIAL PRIMARY KEY,
            root_memory_id   UUID NOT NULL REFERENCES memory_maps(root_memory_id) ON DELETE CASCADE,
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            memory_id        UUID,
            event            TEXT NOT NULL,
            actor            TEXT NOT NULL,
            payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    )
    op.execute(
        sa.text("CREATE INDEX memory_map_events_root_idx ON memory_map_events(root_memory_id)")
    )

    # ---- RLS (canonical safe_current_tenant_id pattern, migration 0034) ----
    for table in ("memory_maps", "memory_map_membership", "memory_map_events"):
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY {table}_select ON {table} FOR SELECT TO mem_app "
                "USING (tenant_id = safe_current_tenant_id())"
            )
        )
        op.execute(
            sa.text(
                f"CREATE POLICY {table}_insert ON {table} FOR INSERT TO mem_app "
                "WITH CHECK (tenant_id = safe_current_tenant_id())"
            )
        )
        op.execute(
            sa.text(
                f"CREATE POLICY {table}_update ON {table} FOR UPDATE TO mem_app "
                "USING (tenant_id = safe_current_tenant_id()) "
                "WITH CHECK (tenant_id = safe_current_tenant_id())"
            )
        )
        op.execute(
            sa.text(
                f"CREATE POLICY {table}_delete ON {table} FOR DELETE TO mem_app "
                "USING (tenant_id = safe_current_tenant_id())"
            )
        )
        op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO mem_app"))

    # memory_map_events.id is a sequence — app role needs USAGE to insert.
    op.execute(sa.text("GRANT USAGE, SELECT ON SEQUENCE memory_map_events_id_seq TO mem_app"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS memory_map_events"))
    op.execute(sa.text("DROP TABLE IF EXISTS memory_map_membership"))
    op.execute(sa.text("DROP TABLE IF EXISTS memory_maps"))
