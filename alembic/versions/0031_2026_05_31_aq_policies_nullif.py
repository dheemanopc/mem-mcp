"""Make async_write_queue RLS cast safe via NULLIF (handles '' post-ROLLBACK).

Migrations 0028 + 0029 + 0030 each tried to make the drain (system_tx,
no GUC) work. The escape clause `OR COALESCE(...) = ''` from 0030
*looked* right but missed a Postgres quirk:

After `SET LOCAL app.current_tenant_id = <UUID>; ROLLBACK`, the custom
GUC does NOT reset to NULL — it resets to ''. Verified on prod:

    SELECT current_setting('app.current_tenant_id', true)
    -- After fresh session: NULL
    -- After SET LOCAL ... ; ROLLBACK on the same session: ''

So the *first* clause `tenant_id = current_setting(...)::uuid` still
crashes with `invalid input syntax for type uuid: ""` whenever the
drain's pool connection has been recycled from a prior tenant_tx
that rolled back.

Fix: wrap the cast with `NULLIF(current_setting(...), '')::uuid`. Now:
- Fresh session (NULL):  NULLIF(NULL, '')   → NULL,        cast = NULL
- Post-rollback (''):    NULLIF('', '')    → NULL,        cast = NULL
- tenant_tx set (UUID):  NULLIF(UUID, '')  → UUID,        cast = UUID

In all three cases the cast is safe. The escape clause `OR COALESCE(...) = ''`
is kept so NULL/'' GUC matches all rows for the drain.

Revision ID: 0031_aq_policies_nullif
Revises: 0030_aq_policies_null_guc
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0031_aq_policies_nullif"
down_revision = "0030_aq_policies_null_guc"
branch_labels = None
depends_on = None


_USING_EXPR = """
    tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
    OR COALESCE(current_setting('app.current_tenant_id', true), '') = ''
"""


def upgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS async_write_queue_select ON async_write_queue"))
    op.execute(
        sa.text(f"""
        CREATE POLICY async_write_queue_select ON async_write_queue
            FOR SELECT TO mem_app
            USING ({_USING_EXPR})
    """)
    )

    op.execute(sa.text("DROP POLICY IF EXISTS async_write_queue_update ON async_write_queue"))
    op.execute(
        sa.text(f"""
        CREATE POLICY async_write_queue_update ON async_write_queue
            FOR UPDATE TO mem_app
            USING ({_USING_EXPR})
            WITH CHECK ({_USING_EXPR})
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
