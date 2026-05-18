"""Add signup_requests table for self-serve registration flow with admin approval."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_add_signup_requests"
down_revision = "0004_add_skill_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create signup_requests table."""
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS citext"))

    op.execute(
        sa.text("""
        CREATE TABLE signup_requests (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email                CITEXT NOT NULL UNIQUE,
            name                 TEXT,
            reason               TEXT,
            client_intent        TEXT[] NOT NULL DEFAULT '{}',
            status               TEXT NOT NULL DEFAULT 'awaiting_verification'
                                 CHECK (status IN ('awaiting_verification','pending','approved','rejected','expired')),
            submitted_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            reviewed_at          TIMESTAMPTZ,
            reviewed_by          TEXT,
            review_notes         TEXT,
            email_verified_at    TIMESTAMPTZ,
            verify_token_hash    TEXT,
            verify_expires_at    TIMESTAMPTZ,
            ip_address           INET,
            user_agent           TEXT
        )
        """)
    )
    op.execute(
        sa.text(
            "CREATE INDEX idx_signup_requests_status ON signup_requests(status, submitted_at DESC)"
        )
    )
    op.execute(sa.text("CREATE INDEX idx_signup_requests_email ON signup_requests(email)"))
    op.execute(sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON signup_requests TO mem_app"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS signup_requests"))
