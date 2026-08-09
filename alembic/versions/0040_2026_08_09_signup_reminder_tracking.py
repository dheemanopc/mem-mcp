"""Add verification-reminder tracking to signup_requests.

Supports the reconcile_signup_backlog job: two columns let the job throttle
re-verification nudges (cap the number of reminders, enforce a cooldown between
them) so stuck ``awaiting_verification`` applicants are unblocked without being
spammed. See BUG 2026-08-09 — admin signup queue empty while applicants blocked.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0040_signup_reminder_tracking"
down_revision = "0039_chunk_build_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add reminder_count + last_reminder_at to signup_requests."""
    op.execute(
        sa.text(
            "ALTER TABLE signup_requests "
            "ADD COLUMN reminder_count INT NOT NULL DEFAULT 0, "
            "ADD COLUMN last_reminder_at TIMESTAMPTZ"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE signup_requests "
            "DROP COLUMN IF EXISTS reminder_count, "
            "DROP COLUMN IF EXISTS last_reminder_at"
        )
    )
