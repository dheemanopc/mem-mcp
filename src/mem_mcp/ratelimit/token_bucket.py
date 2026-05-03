"""Token bucket rate limiter for per-minute quota enforcement (T-7.9).

Per-process in-memory rate limiter. With 2+ workers, effective ceiling is
multiplied by worker count (acceptable for single-VM v1 per LLD §4.8).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Final

# Process-wide bucket registry keyed by string (tenant:{id}:write, tenant:{id}:read, etc.)
_BUCKETS: dict[str, TokenBucket] = {}


class TokenBucket:
    """Sliding-window token bucket for per-minute rate limiting.

    Tokens refill at `refill_per_sec` up to `capacity`. Use `now` callable
    for testability (defaults to `time.monotonic()`).
    """

    def __init__(
        self,
        capacity: int,
        refill_per_sec: float,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize a token bucket.

        Args:
            capacity: Max tokens in the bucket.
            refill_per_sec: Tokens refilled per second.
            now: Callable returning current time (for testing). Defaults to time.monotonic.
        """
        self._capacity: Final[int] = capacity
        self._refill_per_sec: Final[float] = refill_per_sec
        self._now: Final[Callable[[], float]] = now

        self._tokens: float = float(capacity)
        self._last_update: float = now()

    def try_take(self, n: int = 1) -> bool:
        """Try to take n tokens. Returns True if successful, False if insufficient.

        Args:
            n: Number of tokens to try to take.

        Returns:
            True if took n tokens, False otherwise (state unchanged on failure).
        """
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    def time_to_next(self, n: int = 1) -> float:
        """Return seconds until n tokens are available.

        Args:
            n: Number of tokens to wait for.

        Returns:
            Seconds to wait. Returns 0 if n tokens already available.
        """
        self._refill()
        if self._tokens >= n:
            return 0.0
        # How many tokens needed?
        needed = n - self._tokens
        # At refill_per_sec, how long to get that many?
        return needed / self._refill_per_sec

    def _refill(self) -> None:
        """Refill tokens based on elapsed time since last update."""
        now = self._now()
        elapsed = now - self._last_update
        if elapsed > 0:
            new_tokens = self._tokens + elapsed * self._refill_per_sec
            self._tokens = min(new_tokens, float(self._capacity))
            self._last_update = now


def get_bucket(key: str, capacity: int, refill_per_sec: float) -> TokenBucket:
    """Get or create a token bucket for the given key.

    The bucket is created on first call; subsequent calls with the same key
    return the existing bucket (capacity and refill_per_sec are ignored if
    the bucket already exists).

    Args:
        key: Unique bucket key (e.g., "tenant:<uuid>:write").
        capacity: Max tokens (ignored if bucket exists).
        refill_per_sec: Refill rate (ignored if bucket exists).

    Returns:
        The TokenBucket instance for the key.
    """
    if key not in _BUCKETS:
        _BUCKETS[key] = TokenBucket(capacity, refill_per_sec)
    return _BUCKETS[key]


def _reset_buckets_for_tests() -> None:
    """Clear the bucket registry. For use in test fixtures only."""
    _BUCKETS.clear()
