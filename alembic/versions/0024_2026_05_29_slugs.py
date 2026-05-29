"""slugs — stable human-readable identifiers for ratified decisions + facts.

Per memsys foundation 1c47bd99 + proposal ratification 99eacba4 + test plan
ratification 6d3b4bfe. Slugs are immutable once assigned; they FOLLOW the
current canonical version on supersession (UPDATE memory_id when a new
version supersedes). Old versions remain addressable via UUID.

Scope: (team_id, resource_type, slug) — a team can have one 'dod-design'
decision and one 'dod-design' fact; two decisions named 'dod-design' need
disambiguation (handled by PK+retry suffix in code).

Per amendment #11: 64-char cap chosen for AI-token economics, not URL
aesthetics. 64 chars ≈ 15-20 tokens; cheaper context for AI agents
repeatedly citing slugs in references.
Per amendment #12: non-ASCII slugs rejected for now.
Per amendment #14: serialization handled by the PK unique constraint +
in-transaction retry loop in code (src/mem_mcp/teams/slugs.py — stage B).

Revision ID: 0024_slugs
Revises: 0023_user_effective_team_access
Create Date: 2026-05-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0024_slugs"
down_revision = "0023_user_effective_team_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        CREATE TABLE slugs (
            team_id        UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            resource_type  TEXT NOT NULL CHECK (resource_type IN ('decision', 'fact')),
            slug           TEXT NOT NULL CHECK (
                length(slug) BETWEEN 1 AND 64
                AND slug ~ '^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$'
            ),
            memory_id      UUID NOT NULL REFERENCES memories(id) ON DELETE RESTRICT,
            title          TEXT NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (team_id, resource_type, slug)
        )
    """)
    )

    # Reverse lookup: "what's the slug pointing at this memory?"
    op.execute(sa.text("CREATE INDEX slugs_memory_id_idx ON slugs(memory_id)"))

    op.execute(sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON slugs TO mem_app"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS slugs"))
