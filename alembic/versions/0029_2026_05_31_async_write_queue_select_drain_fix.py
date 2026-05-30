"""Fix async_write_queue_select RLS to allow drain (system_tx, empty GUC).

Migration 0028 added `OR current_setting(...) = ''` to the UPDATE policy
so the drain's UPDATE could fire under system_tx. But the drain's UPDATE
contains a sub-SELECT (FOR UPDATE SKIP LOCKED), which hits the SELECT
policy first. The SELECT policy as shipped is strict
`tenant_id = current_setting(...)::uuid` — under empty GUC, that's
`''::uuid`, which either crashes or matches nothing. Drain claim_batch
silently returns 0 rows.

Fix: mirror the UPDATE policy's escape clause on the SELECT policy so
system_tx (empty GUC) sees all rows. Caller visibility (mem_app with a
GUC set) is unchanged.

Revision ID: 0029_aq_select_drain_fix
Revises: 0028_async_write_queue
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0029_aq_select_drain_fix"
down_revision = "0028_async_write_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS async_write_queue_select ON async_write_queue"))
    op.execute(
        sa.text("""
        CREATE POLICY async_write_queue_select ON async_write_queue
            FOR SELECT TO mem_app
            USING (
                tenant_id = current_setting('app.current_tenant_id', true)::uuid
                OR current_setting('app.current_tenant_id', true) = ''
            )
    """)
    )


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS async_write_queue_select ON async_write_queue"))
    op.execute(
        sa.text("""
        CREATE POLICY async_write_queue_select ON async_write_queue
            FOR SELECT TO mem_app
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
    """)
    )
