"""Tests for mem_mcp.auth.jwks (T-4.1).

No real HTTP / Cognito — fake JwksFetcher + injected clock.
"""

from __future__ import annotations

import pytest

from mem_mcp.auth.jwks import (
    HttpxJwksFetcher,
    JwksCache,
    JwksError,
    JwksFetcher,
    JwksPayload,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeFetcher:
    """Returns canned payloads; counts calls; can be reprogrammed mid-test."""

    def __init__(self, payloads: list[JwksPayload]) -> None:
        self.payloads = payloads  # consumed in order
        self.calls = 0

    async def fetch(self) -> JwksPayload:
        self.calls += 1
        if not self.payloads:
            raise AssertionError("FakeFetcher exhausted")
        return self.payloads.pop(0)


class FakeFetcherStatic:
    """Returns the same payload on every call."""

    def __init__(self, payload: JwksPayload) -> None:
        self.payload = payload
        self.calls = 0

    async def fetch(self) -> JwksPayload:
        self.calls += 1
        return self.payload


class FailingFetcher:
    def __init__(self, error: JwksError) -> None:
        self.error = error
        self.calls = 0

    async def fetch(self) -> JwksPayload:
        self.calls += 1
        raise self.error


class ManualClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _key(kid: str) -> dict[str, str]:
    return {"kid": kid, "kty": "RSA", "n": "x", "e": "AQAB", "alg": "RS256", "use": "sig"}


def _payload(*kids: str) -> JwksPayload:
    return {"keys": [_key(k) for k in kids]}  # type: ignore[misc]


# ---------------------------------------------------------------------------
# JwksCache.get_key
# ---------------------------------------------------------------------------


class TestGetKey:
    @pytest.mark.asyncio
    async def test_first_call_fetches(self) -> None:
        fetcher = FakeFetcherStatic(_payload("k1"))
        cache = JwksCache(fetcher, ttl_seconds=60, clock=ManualClock())
        key = await cache.get_key("k1")
        assert key["kid"] == "k1"
        assert fetcher.calls == 1

    @pytest.mark.asyncio
    async def test_subsequent_call_uses_cache(self) -> None:
        fetcher = FakeFetcherStatic(_payload("k1"))
        clock = ManualClock()
        cache = JwksCache(fetcher, ttl_seconds=60, clock=clock)
        await cache.get_key("k1")
        await cache.get_key("k1")
        assert fetcher.calls == 1  # cached

    @pytest.mark.asyncio
    async def test_ttl_expiry_triggers_refresh(self) -> None:
        fetcher = FakeFetcherStatic(_payload("k1"))
        clock = ManualClock()
        cache = JwksCache(fetcher, ttl_seconds=60, clock=clock)
        await cache.get_key("k1")
        clock.advance(60)  # exactly at TTL → stale
        await cache.get_key("k1")
        assert fetcher.calls == 2

    @pytest.mark.asyncio
    async def test_kid_miss_triggers_refresh_then_returns(self) -> None:
        # First fetch returns only k1; second (after kid miss) returns k1 + k2
        fetcher = FakeFetcher([_payload("k1"), _payload("k1", "k2")])
        cache = JwksCache(fetcher, ttl_seconds=3600, clock=ManualClock())
        # Warm cache with k1
        await cache.get_key("k1")
        assert fetcher.calls == 1
        # Ask for k2 (not in cache) — triggers refresh
        key = await cache.get_key("k2")
        assert key["kid"] == "k2"
        assert fetcher.calls == 2

    @pytest.mark.asyncio
    async def test_kid_still_missing_after_refresh_raises(self) -> None:
        fetcher = FakeFetcher([_payload("k1"), _payload("k1")])
        cache = JwksCache(fetcher, ttl_seconds=3600, clock=ManualClock())
        await cache.get_key("k1")  # warm
        with pytest.raises(JwksError) as exc_info:
            await cache.get_key("missing")
        assert exc_info.value.code == "unknown_kid"
        assert fetcher.calls == 2  # one for warm + one refresh

    @pytest.mark.asyncio
    async def test_fetch_failure_propagates(self) -> None:
        fetcher = FailingFetcher(JwksError("fetch_failed", "boom"))
        cache = JwksCache(fetcher)
        with pytest.raises(JwksError) as exc_info:
            await cache.get_key("k1")
        assert exc_info.value.code == "fetch_failed"


# ---------------------------------------------------------------------------
# JwksCache.refresh
# ---------------------------------------------------------------------------


class TestRefresh:
    @pytest.mark.asyncio
    async def test_explicit_refresh_resets_age(self) -> None:
        fetcher = FakeFetcherStatic(_payload("k1"))
        clock = ManualClock()
        cache = JwksCache(fetcher, ttl_seconds=60, clock=clock)
        await cache.get_key("k1")  # call 1
        clock.advance(30)  # half TTL
        await cache.refresh()  # call 2
        clock.advance(30)  # 60s since first warm — but only 30s since refresh
        await cache.get_key("k1")  # cached, no call 3
        assert fetcher.calls == 2


# ---------------------------------------------------------------------------
# JwksError
# ---------------------------------------------------------------------------


class TestJwksError:
    def test_code_attribute(self) -> None:
        e = JwksError("unknown_kid", "missing")
        assert e.code == "unknown_kid"
        assert "missing" in str(e)

    def test_default_message_uses_code(self) -> None:
        e = JwksError("fetch_failed")
        assert "fetch_failed" in str(e)


# ---------------------------------------------------------------------------
# HttpxJwksFetcher (constructor only — real HTTP not exercised)
# ---------------------------------------------------------------------------


class TestHttpxJwksFetcherShape:
    def test_url_construction(self) -> None:
        f = HttpxJwksFetcher(region="ap-south-1", user_pool_id="ap-south-1_TESTPOOL")
        assert (
            f.url
            == "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_TESTPOOL/.well-known/jwks.json"
        )

    def test_default_timeout(self) -> None:
        f = HttpxJwksFetcher(region="r", user_pool_id="u")
        assert f.timeout_seconds == 5.0

    def test_custom_timeout(self) -> None:
        f = HttpxJwksFetcher(region="r", user_pool_id="u", timeout_seconds=10.0)
        assert f.timeout_seconds == 10.0

    def test_satisfies_protocol(self) -> None:
        # Structural typing check (mypy enforces; runtime smoke)
        f: JwksFetcher = HttpxJwksFetcher(region="r", user_pool_id="u")
        assert hasattr(f, "fetch")
