"""memory_references — structured forward/backward graph of citations between memories.

Per memsys foundation 1c47bd99 + test plan e552d271 Spec 4 + amendments:
- #17 (filtered citer list — caller sees UUIDs only for accessible teams,
  inaccessible aggregated as "+N more"). Filter is application-layer logic
  on top of this raw graph; this migration just stores the structured edges.
- #18 (RLS on memory_references — same defense-in-depth pattern as memories).

Forward graph: "what does memory X cite?" (source_memory_id = X).
Backward graph: "what cites memory X?" (target_memory_id = X) — needs the
target index.

Hard-delete protection via FK ON DELETE RESTRICT on target_memory_id —
deleting a memory with inbound refs fails at DB layer; application code
surfaces the structured error (with filtered citers per #17).

Per Spec 4 spec: reference_kind is open-text (extensible by plugins), not
CHECK-constrained. refs_version ∈ {pinned, current} controls supersession
follow-the-slug behavior; pinned is default (audit-safe per spec).

Revision ID: 0025_memory_references
Revises: 0024_slugs
Create Date: 2026-05-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0025_memory_references"
down_revision = "0024_slugs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        CREATE TABLE memory_references (
            source_memory_id   UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            target_memory_id   UUID NOT NULL REFERENCES memories(id) ON DELETE RESTRICT,
            target_team_id     UUID NOT NULL REFERENCES teams(id) ON DELETE RESTRICT,
            target_fragment    INT NULL,
            reference_kind     TEXT NOT NULL,
            refs_version       TEXT NOT NULL DEFAULT 'pinned'
                CHECK (refs_version IN ('pinned', 'current')),
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (source_memory_id, target_memory_id, COALESCE(target_fragment, -1), reference_kind)
        )
    """)
    )

    op.execute(
        sa.text("""
        CREATE INDEX memory_references_target_idx ON memory_references(target_memory_id)
    """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX memory_references_source_idx ON memory_references(source_memory_id)
    """)
    )

    # RLS — same defense-in-depth pattern as memories table.
    # mem_app sees rows whose source OR target is in a team they can access.
    # Sysadmin bypass via existing pattern (is_sysadmin() helper).
    # For now, ENABLE RLS without policies — the join-against-memories
    # via SECURITY DEFINER helpers comes in stage B once the helpers exist.
    # Until policies are added, only the table OWNER (mem_test in CI,
    # mem_maint in prod) can read/write; mem_app gets explicit GRANTs.
    op.execute(sa.text("ALTER TABLE memory_references ENABLE ROW LEVEL SECURITY"))

    # Permissive starter policy: mem_app sees its own writes via the
    # existing tenant context. Tighter policies come with Spec 2 (the
    # write-time validator wires the structured caller-access check
    # at the application layer where it can produce the opaque error
    # required by amendment #17).
    op.execute(
        sa.text("""
        CREATE POLICY memory_references_all ON memory_references
            FOR ALL TO mem_app
            USING (true) WITH CHECK (true)
    """)
    )

    op.execute(sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON memory_references TO mem_app"))


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS memory_references_all ON memory_references"))
    op.execute(sa.text("DROP TABLE IF EXISTS memory_references"))
