"""Add localhost callback to algotrade's allowed_redirect_uris.

Revision ID: 0008_algotrade_localhost
Revises: 0007_kite_intents
Create Date: 2026-05-18

Algotrade dev/smoke testing runs on http://localhost:8080. Without this entry
DCR rejects the registration with invalid_redirect_uri. Idempotent: re-running
is a no-op.
"""

import sqlalchemy as sa

from alembic import op

revision = "0008_algotrade_localhost"
down_revision = "0007_kite_intents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE allowed_software "
            "SET allowed_redirect_uris = "
            "  CASE "
            "    WHEN 'http://localhost:8080/memsys/callback' = ANY(allowed_redirect_uris) "
            "      THEN allowed_redirect_uris "
            "    ELSE array_append(allowed_redirect_uris, 'http://localhost:8080/memsys/callback') "
            "  END "
            "WHERE software_id = 'algotrade'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE allowed_software "
            "SET allowed_redirect_uris = array_remove(allowed_redirect_uris, 'http://localhost:8080/memsys/callback') "
            "WHERE software_id = 'algotrade'"
        )
    )
