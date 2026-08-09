"""DB CRUD for signup_requests. Pure async functions; no business logic."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]

VERIFY_TOKEN_TTL_HOURS = 24


@dataclass(frozen=True)
class SignupRequest:
    id: UUID
    email: str
    name: str | None
    reason: str | None
    client_intent: list[str]
    status: str
    submitted_at: datetime
    reviewed_at: datetime | None
    reviewed_by: str | None
    review_notes: str | None
    email_verified_at: datetime | None
    verify_expires_at: datetime | None


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _row_to_signup_request(row: Any) -> SignupRequest:
    return SignupRequest(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        reason=row["reason"],
        client_intent=list(row["client_intent"] or []),
        status=row["status"],
        submitted_at=row["submitted_at"],
        reviewed_at=row["reviewed_at"],
        reviewed_by=row["reviewed_by"],
        review_notes=row["review_notes"],
        email_verified_at=row["email_verified_at"],
        verify_expires_at=row["verify_expires_at"],
    )


async def upsert_request(
    pool: asyncpg.Pool,
    *,
    email: str,
    name: str | None,
    reason: str | None,
    client_intent: list[str],
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[SignupRequest, str]:
    """Insert or update a signup request. Returns (record, raw_verify_token).

    If a row with this email already exists in awaiting_verification or pending,
    its token is reset and TTL extended (re-submit / resend). If status is
    approved or rejected, raises ValueError (caller should respond 409).
    """
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    now = datetime.now(tz=UTC)
    expires = now + timedelta(hours=VERIFY_TOKEN_TTL_HOURS)

    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id, status FROM signup_requests WHERE email = $1", email
        )
        if existing is not None and existing["status"] in ("approved", "rejected"):
            raise ValueError(f"signup request already {existing['status']}")
        row = await conn.fetchrow(
            """
            INSERT INTO signup_requests
                (email, name, reason, client_intent, status,
                 verify_token_hash, verify_expires_at, ip_address, user_agent)
            VALUES ($1, $2, $3, $4, 'awaiting_verification', $5, $6, $7, $8)
            ON CONFLICT (email) DO UPDATE SET
                name = COALESCE(EXCLUDED.name, signup_requests.name),
                reason = COALESCE(EXCLUDED.reason, signup_requests.reason),
                client_intent = EXCLUDED.client_intent,
                verify_token_hash = EXCLUDED.verify_token_hash,
                verify_expires_at = EXCLUDED.verify_expires_at,
                ip_address = EXCLUDED.ip_address,
                user_agent = EXCLUDED.user_agent,
                status = CASE
                    WHEN signup_requests.status IN ('awaiting_verification','pending','expired')
                    THEN 'awaiting_verification'
                    ELSE signup_requests.status
                END,
                email_verified_at = CASE
                    WHEN signup_requests.status IN ('awaiting_verification','expired')
                    THEN NULL
                    ELSE signup_requests.email_verified_at
                END
            RETURNING id, email, name, reason, client_intent, status, submitted_at,
                      reviewed_at, reviewed_by, review_notes, email_verified_at,
                      verify_expires_at
            """,
            email,
            name,
            reason,
            client_intent,
            token_hash,
            expires,
            ip_address,
            user_agent,
        )
    return _row_to_signup_request(row), raw_token


async def verify_token(pool: asyncpg.Pool, *, raw_token: str) -> SignupRequest | None:
    """Mark request as verified if token matches and not expired. Returns the
    updated record on success, None on miss/expired/already-verified."""
    token_hash = _hash_token(raw_token)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE signup_requests
            SET status = 'pending',
                email_verified_at = now(),
                verify_token_hash = NULL,
                verify_expires_at = NULL
            WHERE verify_token_hash = $1
              AND verify_expires_at > now()
              AND status = 'awaiting_verification'
            RETURNING id, email, name, reason, client_intent, status, submitted_at,
                      reviewed_at, reviewed_by, review_notes, email_verified_at,
                      verify_expires_at
            """,
            token_hash,
        )
    return _row_to_signup_request(row) if row else None


async def list_requests(
    pool: asyncpg.Pool,
    *,
    status_filter: str | None = None,
    limit: int = 50,
) -> list[SignupRequest]:
    async with pool.acquire() as conn:
        if status_filter:
            rows = await conn.fetch(
                "SELECT id, email, name, reason, client_intent, status, submitted_at, "
                "reviewed_at, reviewed_by, review_notes, email_verified_at, verify_expires_at "
                "FROM signup_requests WHERE status = $1 "
                "ORDER BY submitted_at DESC LIMIT $2",
                status_filter,
                limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, email, name, reason, client_intent, status, submitted_at, "
                "reviewed_at, reviewed_by, review_notes, email_verified_at, verify_expires_at "
                "FROM signup_requests ORDER BY submitted_at DESC LIMIT $1",
                limit,
            )
    return [_row_to_signup_request(r) for r in rows]


async def get_by_id(pool: asyncpg.Pool, *, request_id: UUID) -> SignupRequest | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, name, reason, client_intent, status, submitted_at, "
            "reviewed_at, reviewed_by, review_notes, email_verified_at, verify_expires_at "
            "FROM signup_requests WHERE id = $1",
            request_id,
        )
    return _row_to_signup_request(row) if row else None


@dataclass(frozen=True)
class StuckAwaitingRequest:
    """A signup request stuck in awaiting_verification, for the backlog sweep."""

    id: UUID
    email: str
    name: str | None
    submitted_at: datetime
    verify_expires_at: datetime | None
    reminder_count: int


async def expire_stale_awaiting(
    pool: asyncpg.Pool,
    *,
    dry_run: bool = False,
) -> list[str]:
    """Move awaiting_verification rows whose verify token has lapsed to 'expired'.

    These are applicants who never clicked the verification link before the TTL
    ran out; leaving them in awaiting_verification hides them in an invisible
    limbo. Marking them 'expired' makes the outcome explicit and queryable.

    Returns the list of affected emails. When dry_run, selects (does not mutate)
    the rows that would expire.
    """
    async with pool.acquire() as conn:
        if dry_run:
            rows = await conn.fetch(
                "SELECT email FROM signup_requests "
                "WHERE status = 'awaiting_verification' AND verify_expires_at < now()"
            )
        else:
            rows = await conn.fetch(
                "UPDATE signup_requests SET status = 'expired' "
                "WHERE status = 'awaiting_verification' AND verify_expires_at < now() "
                "RETURNING email"
            )
    return [r["email"] for r in rows]


async def list_stuck_awaiting(
    pool: asyncpg.Pool,
    *,
    min_age_minutes: int,
    reminder_cooldown_hours: int,
    max_reminders: int,
    limit: int = 100,
) -> list[StuckAwaitingRequest]:
    """List awaiting_verification requests eligible for a re-verification nudge.

    Eligible = token still valid, submitted long enough ago that the original
    email had a fair chance (min_age_minutes grace), not yet at the reminder cap,
    and past the cooldown since the last reminder.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, email, name, submitted_at, verify_expires_at, reminder_count
            FROM signup_requests
            WHERE status = 'awaiting_verification'
              AND verify_expires_at > now()
              AND submitted_at < now() - make_interval(mins => $1)
              AND reminder_count < $2
              AND (last_reminder_at IS NULL
                   OR last_reminder_at < now() - make_interval(hours => $3))
            ORDER BY submitted_at ASC
            LIMIT $4
            """,
            min_age_minutes,
            max_reminders,
            reminder_cooldown_hours,
            limit,
        )
    return [
        StuckAwaitingRequest(
            id=r["id"],
            email=r["email"],
            name=r["name"],
            submitted_at=r["submitted_at"],
            verify_expires_at=r["verify_expires_at"],
            reminder_count=r["reminder_count"],
        )
        for r in rows
    ]


async def issue_verification_reminder(
    pool: asyncpg.Pool,
    *,
    request_id: UUID,
    ttl_hours: int = VERIFY_TOKEN_TTL_HOURS,
) -> str | None:
    """Mint a fresh verify token, extend the TTL, and record the reminder.

    Returns the new raw token to embed in the resent email, or None if the row
    is no longer in awaiting_verification (raced with a verify/expire). The
    guard in the WHERE clause makes this safe to call concurrently with the
    public verify endpoint and the expiry sweep.
    """
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    now = datetime.now(tz=UTC)
    expires = now + timedelta(hours=ttl_hours)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE signup_requests
            SET verify_token_hash = $2,
                verify_expires_at = $3,
                reminder_count = reminder_count + 1,
                last_reminder_at = now()
            WHERE id = $1 AND status = 'awaiting_verification'
            RETURNING id
            """,
            request_id,
            token_hash,
            expires,
        )
    return raw_token if row else None


async def count_by_status(pool: asyncpg.Pool, *, status: str) -> int:
    """Return the number of signup_requests currently in the given status."""
    async with pool.acquire() as conn:
        val = await conn.fetchval("SELECT count(*) FROM signup_requests WHERE status = $1", status)
    return int(val or 0)


async def mark_reviewed(
    pool: asyncpg.Pool,
    *,
    request_id: UUID,
    status: str,
    reviewer: str,
    notes: str | None,
) -> SignupRequest | None:
    """Set status to approved or rejected. Idempotent only when status is
    still 'pending'. Returns updated row or None if request was not in
    pending state (caller responds 409)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE signup_requests
            SET status = $2, reviewed_at = now(), reviewed_by = $3, review_notes = $4
            WHERE id = $1 AND status = 'pending'
            RETURNING id, email, name, reason, client_intent, status, submitted_at,
                      reviewed_at, reviewed_by, review_notes, email_verified_at,
                      verify_expires_at
            """,
            request_id,
            status,
            reviewer,
            notes,
        )
    return _row_to_signup_request(row) if row else None
