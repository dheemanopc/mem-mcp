"""Persist caller identity in async_write_queue so drain replays as the caller.

When write_async submitted a row, the caller's client_id and identity_id
were dropped on the floor. At drain time the daemon synthesized
`client_id = "async-drain"` and `identity_id = tenant_id`. The resulting
INSERT into memories failed the FK to oauth_clients (no row for
"async-drain"). Verified on prod 2026-05-31 with smoke row
a6e096bc → state=failed / ForeignKeyViolationError.

Fix: add the two columns. Both NULL-allowed because (a) we can't
backfill existing queue rows with the original caller, and (b) the
drain has a fallback for legacy rows (use a sentinel client + the
tenant_id as identity_id, same as today, which still fails but
explicitly).

Revision ID: 0032_aq_caller_identity
Revises: 0031_aq_policies_nullif
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0032_aq_caller_identity"
down_revision = "0031_aq_policies_nullif"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        ALTER TABLE async_write_queue
            ADD COLUMN client_id   TEXT,
            ADD COLUMN identity_id UUID
    """)
    )


def downgrade() -> None:
    op.execute(
        sa.text("""
        ALTER TABLE async_write_queue
            DROP COLUMN IF EXISTS client_id,
            DROP COLUMN IF EXISTS identity_id
    """)
    )
