"""Add source marker to signup_requests.

Distinguishes how a request entered the queue. 'form' = the self-serve Request
Access form (email proven via SES verify link). 'google_denied' = captured at
the Cognito PreSignUp choke point when someone signed in with Google but was not
on the invited_emails allowlist — Google already verified the email, so these
land straight in 'pending' for operator review. See BUG 2026-08-09.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0041_signup_request_source"
down_revision = "0040_signup_reminder_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("ALTER TABLE signup_requests ADD COLUMN source TEXT NOT NULL DEFAULT 'form'")
    )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE signup_requests DROP COLUMN IF EXISTS source"))
