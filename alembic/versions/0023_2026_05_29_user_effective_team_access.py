"""user_effective_team_access — precomputed (user x resource_team) access lookup.

Per memsys foundation 1c47bd99 (Spec 5) and proposal amendment #23
(TABLE not MATERIALIZED VIEW: PG MVs don't support incremental refresh;
regular table + trigger maintenance is what we actually want).

This migration creates the EMPTY TABLE + indexes + GRANT only. Trigger
functions + async cascade NOTIFY emitter + Python sync refresh helpers
ship in subsequent migrations / commits within the same Spec 5 PR.

Revision ID: 0023_user_effective_team_access
Revises: 0022_team_role_assignments
Create Date: 2026-05-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023_user_effective_team_access"
down_revision = "0022_team_role_assignments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        CREATE TABLE user_effective_team_access (
            user_id              UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            resource_team_id     UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            effective_role_class TEXT NOT NULL CHECK (effective_role_class IN ('internal', 'external', 'service')),
            effective_perms      TEXT[] NOT NULL,
            path_count           INT NOT NULL CHECK (path_count >= 1),
            refreshed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, resource_team_id)
        )
    """)
    )

    # Index on resource_team_id for queries like "who has access to team X"
    op.execute(
        sa.text(
            "CREATE INDEX uea_resource_team_idx ON user_effective_team_access(resource_team_id)"
        )
    )

    # Index on refreshed_at for the periodic reconciler ("find rows stale > N minutes")
    op.execute(
        sa.text("CREATE INDEX uea_refreshed_at_idx ON user_effective_team_access(refreshed_at)")
    )

    op.execute(sa.text("GRANT SELECT ON user_effective_team_access TO mem_app"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS user_effective_team_access"))
