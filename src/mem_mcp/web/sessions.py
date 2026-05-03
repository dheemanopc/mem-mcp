"""Web session lifecycle (T-8.2, LLD §4.11.1).

Cookie `mem_session` (HttpOnly, Secure, SameSite=Lax, 7d). Value 32 bytes urlsafe;
DB key `sha256(value)`. `last_seen_at` updated if > 60s old.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from mem_mcp.db.tenant_tx import system_tx

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

SESSION_COOKIE_NAME = "mem_session"
SESSION_TTL = timedelta(days=7)
SESSION_TOUCH_THRESHOLD = timedelta(seconds=60)


def mint_session_value() -> tuple[str, str]:
    """Generate a new session value + its sha256 hash.

    Returns: (raw_urlsafe_value, sha256_hex)
    """
    raw = secrets.token_urlsafe(32)
    sha = hashlib.sha256(raw.encode()).hexdigest()
    return raw, sha


@dataclass(frozen=True)
class CreateSessionResult:
    """Result of create_session."""

    raw_value: str
    expires_at: datetime


@dataclass(frozen=True)
class SessionContext:
    """Validated session context from lookup_session."""

    tenant_id: UUID
    identity_id: UUID


async def create_session(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    identity_id: UUID,
    user_agent: str | None,
    ip_address: str | None,
    now: Callable[[], datetime] | None = None,
) -> CreateSessionResult:
    """Insert web_sessions row. Returns CreateSessionResult(raw_value, expires_at)."""
    if now is None:

        def now() -> datetime:
            return datetime.now(tz=UTC)

    raw, sha = mint_session_value()
    expires_at = now() + SESSION_TTL

    async with system_tx(pool) as conn:
        await conn.execute(
            """
            INSERT INTO web_sessions (session_hash, tenant_id, identity_id, user_agent, ip_address, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            sha,
            tenant_id,
            identity_id,
            user_agent,
            ip_address,
            expires_at,
        )

    return CreateSessionResult(raw_value=raw, expires_at=expires_at)


async def lookup_session(
    pool: asyncpg.Pool,
    raw_value: str,
    now: Callable[[], datetime] | None = None,
) -> SessionContext | None:
    """Look up session by raw value.

    Returns SessionContext if valid (not expired, not revoked).
    Updates last_seen_at if last seen > 60s ago.
    Returns None on miss/expired/revoked.
    """
    if now is None:

        def now() -> datetime:
            return datetime.now(tz=UTC)

    sha = hashlib.sha256(raw_value.encode()).hexdigest()

    async with system_tx(pool) as conn:
        row = await conn.fetchrow(
            """
            SELECT tenant_id, identity_id, expires_at, revoked_at, last_seen_at
            FROM web_sessions
            WHERE session_hash = $1
            """,
            sha,
        )

        if row is None:
            return None

        if row["revoked_at"] is not None:
            return None

        if row["expires_at"] <= now():
            return None

        # Touch last_seen_at if stale (> 60s)
        if (now() - row["last_seen_at"]) > SESSION_TOUCH_THRESHOLD:
            await conn.execute(
                """
                UPDATE web_sessions
                SET last_seen_at = now()
                WHERE session_hash = $1
                """,
                sha,
            )

    return SessionContext(tenant_id=row["tenant_id"], identity_id=row["identity_id"])


async def revoke_session(pool: asyncpg.Pool, raw_value: str) -> None:
    """Mark session revoked (logout)."""
    sha = hashlib.sha256(raw_value.encode()).hexdigest()
    async with system_tx(pool) as conn:
        await conn.execute(
            """
            UPDATE web_sessions
            SET revoked_at = now()
            WHERE session_hash = $1
            """,
            sha,
        )
