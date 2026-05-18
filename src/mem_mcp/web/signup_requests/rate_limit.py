"""Simple bucket rate limiter using the existing rate_limits table."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg  # type: ignore[import-untyped]


async def check_and_increment(
    pool: asyncpg.Pool,
    *,
    key: str,
    bucket_seconds: int,
    limit: int,
) -> bool:
    """Returns True if under limit (and increments), False if over.

    Bucket is fixed-window. After bucket_seconds since bucket_start, count
    resets on the next call.
    """
    now = datetime.now(tz=UTC)
    bucket_start_floor = now - timedelta(seconds=bucket_seconds)
    async with pool.acquire() as conn:
        # Upsert
        await conn.execute(
            """
            INSERT INTO rate_limits (key, bucket_start, count)
            VALUES ($1, $2, 1)
            ON CONFLICT (key) DO UPDATE SET
                bucket_start = CASE WHEN rate_limits.bucket_start < $3 THEN $2 ELSE rate_limits.bucket_start END,
                count = CASE WHEN rate_limits.bucket_start < $3 THEN 1 ELSE rate_limits.count + 1 END
            """,
            key,
            now,
            bucket_start_floor,
        )
        # Read back to check limit
        row = await conn.fetchrow("SELECT count FROM rate_limits WHERE key = $1", key)
        return bool(row and row["count"] <= limit)
