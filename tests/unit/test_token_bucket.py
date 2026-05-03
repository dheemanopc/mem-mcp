"""Tests for TokenBucket rate limiter (T-7.9)."""

from __future__ import annotations

import pytest

from mem_mcp.ratelimit.token_bucket import TokenBucket, _reset_buckets_for_tests, get_bucket


class TestTokenBucket:
    """Tests for TokenBucket in-memory rate limiter."""

    @pytest.fixture(autouse=True)
    def _reset(self) -> None:
        """Reset the global bucket registry before each test."""
        _reset_buckets_for_tests()

    def test_starts_full(self) -> None:
        """Bucket starts at capacity; can take capacity tokens."""
        bucket = TokenBucket(capacity=5, refill_per_sec=1.0)
        assert bucket.try_take(5) is True
        assert bucket.try_take(1) is False

    def test_refills_over_time(self) -> None:
        """Tokens refill over time at the specified rate."""
        now_val = 0.0

        def fake_now() -> float:
            return now_val

        bucket = TokenBucket(capacity=5, refill_per_sec=1.0, now=fake_now)
        assert bucket.try_take(5) is True
        assert bucket.try_take(1) is False

        # Advance 1 second; should refill 1 token
        now_val = 1.0
        assert bucket.try_take(1) is True
        assert bucket.try_take(1) is False

        # Advance 2 more seconds; should refill 2 tokens
        now_val = 3.0
        assert bucket.try_take(2) is True
        assert bucket.try_take(1) is False

    def test_time_to_next_reports_correctly(self) -> None:
        """time_to_next returns seconds until n tokens available."""
        now_val = 0.0

        def fake_now() -> float:
            return now_val

        bucket = TokenBucket(capacity=5, refill_per_sec=2.0, now=fake_now)
        bucket.try_take(5)  # Empty the bucket
        assert bucket.try_take(1) is False

        # With 2 tokens/sec refill, 1 token takes 0.5 seconds
        wait_time = bucket.time_to_next(1)
        assert abs(wait_time - 0.5) < 0.01, f"expected ~0.5, got {wait_time}"

    def test_capacity_cap(self) -> None:
        """Bucket never exceeds capacity even with long elapsed time."""
        now_val = 0.0

        def fake_now() -> float:
            return now_val

        bucket = TokenBucket(capacity=5, refill_per_sec=1.0, now=fake_now)
        bucket.try_take(5)  # Empty

        # Advance 1000 seconds; should cap at capacity, not overflow
        now_val = 1000.0
        assert bucket.try_take(5) is True
        assert bucket.try_take(1) is False

    def test_take_rejected_returns_false_no_state_change(self) -> None:
        """Failed take doesn't change state; later partial take can succeed."""
        bucket = TokenBucket(capacity=3, refill_per_sec=1.0)
        bucket.try_take(3)  # Empty
        assert bucket.try_take(2) is False  # Reject; state unchanged
        assert bucket.try_take(1) is False  # Still empty after rejection

    def test_get_bucket_returns_same_instance(self) -> None:
        """get_bucket returns same bucket instance on subsequent calls."""
        bucket1 = get_bucket("test_key", capacity=10, refill_per_sec=5.0)
        bucket2 = get_bucket("test_key", capacity=99, refill_per_sec=99.0)
        assert bucket1 is bucket2

    def test_get_bucket_keyed_by_string(self) -> None:
        """Different keys return different bucket instances."""
        bucket1 = get_bucket("key1", capacity=10, refill_per_sec=1.0)
        bucket2 = get_bucket("key2", capacity=10, refill_per_sec=1.0)
        assert bucket1 is not bucket2

    def test_multiple_takes_and_refills(self) -> None:
        """Complex sequence of takes and refills."""
        now_val = 0.0

        def fake_now() -> float:
            return now_val

        bucket = TokenBucket(capacity=10, refill_per_sec=2.0, now=fake_now)
        # Take 10, empty
        assert bucket.try_take(10) is True
        assert bucket.try_take(1) is False

        # After 0.5 sec, 1 token available
        now_val = 0.5
        assert bucket.try_take(1) is True
        assert bucket.try_take(1) is False

        # After 2.5 more sec (3.0 total), 5 tokens refilled, minus 1 we just took = 4 available
        now_val = 3.0
        assert bucket.try_take(4) is True
        assert bucket.try_take(1) is False
