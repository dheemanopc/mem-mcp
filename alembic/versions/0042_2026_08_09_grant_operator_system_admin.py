"""Grant system_admin to the operator tenant so the signup queue is reviewable.

BUG 2026-08-09 follow-up: the admin signup queue 403s (rendered as an empty
queue) because the operator's tenant holds no system role. The normal grant path
(bootstrap_first_system_admin) only fires when MEM_MCP_BOOTSTRAP_SYSTEM_ADMIN_EMAIL
is set, which it never was — so no tenant is system_admin and nobody can review
or approve signups. With no shell/env access, the grant has to ride in on a
deploy; this data migration does it idempotently.

Targets the operator's login identity by email. Safe to re-run (ON CONFLICT) and
a no-op if that identity has no provisioned tenant yet.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0042_grant_operator_system_admin"
down_revision = "0041_signup_request_source"
branch_labels = None
depends_on = None

_OPERATOR_EMAIL = "anand1711@gmail.com"
_GRANT_NOTE = "operator grant via migration 0042 (BUG 2026-08-09; bootstrap env var never set)"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO tenant_system_roles (tenant_id, role, granted_by_tenant_id, notes)
            SELECT ti.tenant_id, 'system_admin', CAST(NULL AS uuid), :note
            FROM tenant_identities ti
            WHERE LOWER(ti.email) = LOWER(:email)
            ON CONFLICT ON CONSTRAINT uq_tenant_system_roles_tenant_role DO NOTHING
            """
        ).bindparams(email=_OPERATOR_EMAIL, note=_GRANT_NOTE)
    )


def downgrade() -> None:
    # Remove only the grant this migration created (matched by our note marker).
    op.execute(
        sa.text(
            """
            DELETE FROM tenant_system_roles
            WHERE role = 'system_admin'
              AND notes = :note
              AND tenant_id IN (
                  SELECT tenant_id FROM tenant_identities WHERE LOWER(email) = LOWER(:email)
              )
            """
        ).bindparams(email=_OPERATOR_EMAIL, note=_GRANT_NOTE)
    )
