"""DCR two-tier support: require_secret + allowed_redirect_uris on allowed_software; seed algotrade."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_dcr_two_tier"
down_revision = "0005_add_signup_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE allowed_software "
            "ADD COLUMN require_secret BOOLEAN NOT NULL DEFAULT false"
        )
    )
    op.execute(sa.text("ALTER TABLE allowed_software " "ADD COLUMN allowed_redirect_uris TEXT[]"))
    op.execute(
        sa.text(
            "INSERT INTO allowed_software "
            "(software_id, display_name, vendor, verified, status, "
            "require_secret, allowed_redirect_uris, notes) VALUES "
            "('algotrade', 'AlgoTrade backend', 'dheemantech', true, 'allowed', "
            "true, ARRAY['https://tradeapi.dheemantech.in/memsys/callback'], "
            "'Server-side OAuth client for AI Trader v2 orchestrator; confidential client')"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM allowed_software WHERE software_id = 'algotrade'"))
    op.execute(sa.text("ALTER TABLE allowed_software DROP COLUMN allowed_redirect_uris"))
    op.execute(sa.text("ALTER TABLE allowed_software DROP COLUMN require_secret"))
