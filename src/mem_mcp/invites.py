"""Programmatic helper to insert an email into invited_emails (used by signup approval flow)."""

from __future__ import annotations

import asyncpg  # type: ignore[import-untyped]


async def seed_invite_email(
    pool: asyncpg.Pool,
    *,
    email: str,
    invited_by: str,
    notes: str | None = None,
) -> None:
    """Insert or update an invited email. Idempotent."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO invited_emails (email, invited_by, notes)
            VALUES ($1, $2, $3)
            ON CONFLICT (email) DO UPDATE
            SET invited_by = EXCLUDED.invited_by,
                notes = EXCLUDED.notes,
                consumed_at = NULL
            """,
            email.lower(),
            invited_by,
            notes,
        )
