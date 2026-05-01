"""Seed allowed_software with v1 software entries.

Inserts the initial set of allowed and blocked software per spec §8.4.

References:
- MEMORY_MCP_BUILD_PLAN_V2.md §8.4
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_seed_allowed_software"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Insert seed data for allowed_software table."""
    op.execute(
        sa.text("""
        INSERT INTO allowed_software (software_id, display_name, vendor, verified, status, notes) VALUES
        ('claude-code',  'Claude Code',           'Anthropic', true, 'allowed', 'Anthropic CLI agent'),
        ('claude-ai',    'Claude.ai (web/desktop/mobile)', 'Anthropic', true, 'allowed', 'Anthropic chat surfaces'),
        ('chatgpt',      'ChatGPT (developer connectors)', 'OpenAI',    true, 'allowed', 'OpenAI MCP'),
        ('cursor',       'Cursor',                'Anysphere',     true, 'blocked', 'Reachable by user request'),
        ('perplexity',   'Perplexity',            'Perplexity AI', true, 'blocked', 'Not in v1 scope')
        """)
    )


def downgrade() -> None:
    """Delete seed rows by software_id."""
    op.execute(
        sa.text("""
        DELETE FROM allowed_software WHERE software_id IN
        ('claude-code', 'claude-ai', 'chatgpt', 'cursor', 'perplexity')
        """)
    )
