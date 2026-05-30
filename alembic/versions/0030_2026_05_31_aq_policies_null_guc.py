"""Fix async_write_queue RLS to handle NULL GUC (not just empty string).

Migrations 0028 + 0029 tried to allow drain (system_tx, no GUC set) by
adding `OR current_setting('app.current_tenant_id', true) = ''` to the
SELECT + UPDATE policies. The escape clause is wrong: when the GUC was
never `set_config()`d on the session, `current_setting(..., true)`
returns **NULL**, not ''. So the comparison `... = ''` is NULL and the
OR short-circuits to NULL too — row excluded.

Verified on prod 2026-05-31:
  SELECT current_setting('app.current_tenant_id', true) IS NULL → t
  SELECT current_setting('app.current_tenant_id', true) = ''   → NULL

Fix: use `COALESCE(current_setting(...), '') = ''` so unset GUC reads
as '' and the escape clause fires.

Revision ID: 0030_aq_policies_null_guc
Revises: 0029_aq_select_drain_fix
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0030_aq_policies_null_guc"
down_revision = "0029_aq_select_drain_fix"
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
                OR COALESCE(current_setting('app.current_tenant_id', true), '') = ''
            )
    """)
    )

    op.execute(sa.text("DROP POLICY IF EXISTS async_write_queue_update ON async_write_queue"))
    op.execute(
        sa.text("""
        CREATE POLICY async_write_queue_update ON async_write_queue
            FOR UPDATE TO mem_app
            USING (
                tenant_id = current_setting('app.current_tenant_id', true)::uuid
                OR COALESCE(current_setting('app.current_tenant_id', true), '') = ''
            )
            WITH CHECK (
                tenant_id = current_setting('app.current_tenant_id', true)::uuid
                OR COALESCE(current_setting('app.current_tenant_id', true), '') = ''
            )
    """)
    )


def downgrade() -> None:
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

    op.execute(sa.text("DROP POLICY IF EXISTS async_write_queue_update ON async_write_queue"))
    op.execute(
        sa.text("""
        CREATE POLICY async_write_queue_update ON async_write_queue
            FOR UPDATE TO mem_app
            USING (
                tenant_id = current_setting('app.current_tenant_id', true)::uuid
                OR current_setting('app.current_tenant_id', true) = ''
            )
            WITH CHECK (
                tenant_id = current_setting('app.current_tenant_id', true)::uuid
                OR current_setting('app.current_tenant_id', true) = ''
            )
    """)
    )
