"""roles_catalog table — pluggable role definitions for teams.

Per memsys 1c47bd99 (foundation) and 99eacba4 (amendment #3 preserves owner-vs-admin
distinction). Migration 0021 in the team-foundation sequence.

Revision ID: 0021_roles_catalog
Revises: 0020_plugin_notices
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0021_roles_catalog"
down_revision = "0020_plugin_notices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        CREATE TABLE roles_catalog (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            role_key     TEXT NOT NULL,
            display_name TEXT NOT NULL,
            description  TEXT,
            role_class   TEXT NOT NULL CHECK (role_class IN ('internal','external','service')),
            plugin_id    TEXT NULL,
            permissions  TEXT[] NOT NULL DEFAULT '{}',
            is_active    BOOLEAN NOT NULL DEFAULT true,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (plugin_id, role_key)
        )
    """)
    )
    op.execute(
        sa.text(
            "CREATE INDEX roles_catalog_role_key_idx ON roles_catalog(role_key) WHERE is_active"
        )
    )
    op.execute(sa.text("GRANT SELECT ON roles_catalog TO mem_app"))

    # 7 core roles per amendment #3 (owner has delete, admin does not).
    op.execute(
        sa.text("""
        INSERT INTO roles_catalog (role_key, display_name, description, role_class, permissions) VALUES
        ('owner',       'Owner',        'Full control including team deletion',                    'internal', ARRAY['read','write','admin','delete','audit']),
        ('admin',       'Admin',        'Manage members and team settings (cannot delete team)',   'internal', ARRAY['read','write','admin','audit']),
        ('member',      'Member',       'Read and write team resources',                           'internal', ARRAY['read','write']),
        ('viewer',      'Viewer',       'Read-only access to team resources',                      'internal', ARRAY['read']),
        ('vendor',      'Vendor',       'External vendor with read access to external-scoped resources', 'external', ARRAY['read']),
        ('customer',    'Customer',     'External customer with read access',                      'external', ARRAY['read']),
        ('service-bot', 'Service Bot',  'Automated service account',                               'service',  ARRAY['read','write'])
        ON CONFLICT (plugin_id, role_key) DO NOTHING
    """)
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS roles_catalog"))
