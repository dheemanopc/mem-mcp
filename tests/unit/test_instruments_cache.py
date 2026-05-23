"""Unit tests for InstrumentsCache."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo
from uuid import uuid4

import httpx
import pytest

from mem_mcp.skills.base import SkillError
from mem_mcp.skills.kite.client import KiteClient
from mem_mcp.skills.kite.instruments_cache import InstrumentsCache

# IST timezone
IST = ZoneInfo("Asia/Kolkata")


def _make_mock_client(handler: Any) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient backed by a MockTransport."""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


def _build_instruments_csv(symbols: list[str] | None = None) -> str:
    """Build sample instruments CSV."""
    if symbols is None:
        symbols = ["RELIANCE", "WIPRO", "INFY", "TCS"]

    lines = [
        "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange"
    ]
    for i, symbol in enumerate(symbols):
        token = 100000 + i
        lines.append(f"{token},0,{symbol},{symbol} LTD,100.0,,0,0.05,1,CE,EQUITY,NSE")

    return "\n".join(lines)


class TestInstrumentsCacheLookup:
    """Test basic symbol→token lookup."""

    @pytest.mark.asyncio
    async def test_cache_miss_fetches_and_caches(self) -> None:
        """Test that cache miss triggers a fetch."""
        cache = InstrumentsCache()
        call_count = {"value": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            if "/instruments/NSE" in req.url.path:
                call_count["value"] += 1
                return httpx.Response(200, text=_build_instruments_csv())
            return httpx.Response(404)

        client = KiteClient(http=_make_mock_client(handler))

        # First lookup: cache miss
        token1 = await cache.get_token(
            tenant_id="tenant1",
            symbol="RELIANCE",
            client=client,
            api_key="key",
            access_token="token",
        )

        assert token1 == 100000
        assert call_count["value"] == 1

        # Second lookup: cache hit
        token2 = await cache.get_token(
            tenant_id="tenant1",
            symbol="WIPRO",
            client=client,
            api_key="key",
            access_token="token",
        )

        assert token2 == 100001
        assert call_count["value"] == 1  # No additional fetch

    @pytest.mark.asyncio
    async def test_symbol_not_found(self) -> None:
        """Test that missing symbol raises SkillError."""
        cache = InstrumentsCache()

        def handler(req: httpx.Request) -> httpx.Response:
            if "/instruments/NSE" in req.url.path:
                return httpx.Response(200, text=_build_instruments_csv(["RELIANCE", "WIPRO"]))
            return httpx.Response(404)

        client = KiteClient(http=_make_mock_client(handler))

        with pytest.raises(SkillError, match="not found in NSE instruments master"):
            await cache.get_token(
                tenant_id="tenant1",
                symbol="NONEXISTENT",
                client=client,
                api_key="key",
                access_token="token",
            )

    @pytest.mark.asyncio
    async def test_per_tenant_isolation(self) -> None:
        """Test that different tenants have separate caches."""
        cache = InstrumentsCache()

        def handler(req: httpx.Request) -> httpx.Response:
            if "/instruments/NSE" in req.url.path:
                return httpx.Response(200, text=_build_instruments_csv())
            return httpx.Response(404)

        client = KiteClient(http=_make_mock_client(handler))

        # Fetch for tenant1
        token1 = await cache.get_token(
            tenant_id="tenant1",
            symbol="RELIANCE",
            client=client,
            api_key="key",
            access_token="token",
        )
        assert token1 == 100000

        # Fetch for tenant2
        token2 = await cache.get_token(
            tenant_id="tenant2",
            symbol="RELIANCE",
            client=client,
            api_key="key",
            access_token="token",
        )
        assert token2 == 100000  # Same symbol, same token


class TestCacheRefresh:
    """Test cache staleness and refresh logic."""

    @pytest.mark.asyncio
    async def test_cache_stale_on_date_change(self) -> None:
        """Test that cache is refreshed when IST date changes."""
        cache = InstrumentsCache()
        call_count = {"value": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            if "/instruments/NSE" in req.url.path:
                call_count["value"] += 1
                return httpx.Response(200, text=_build_instruments_csv())
            return httpx.Response(404)

        client = KiteClient(http=_make_mock_client(handler))

        # First fetch
        await cache.get_token(
            tenant_id="tenant1",
            symbol="RELIANCE",
            client=client,
            api_key="key",
            access_token="token",
        )
        assert call_count["value"] == 1

        # Manually set cache to yesterday
        key = ("tenant1", "NSE")
        if key in cache._cache:
            symbol_to_token, _fetched_at = cache._cache[key]
            yesterday = datetime.now(IST) - timedelta(days=1)
            cache._cache[key] = (symbol_to_token, yesterday)

        # Next lookup should trigger refresh
        await cache.get_token(
            tenant_id="tenant1",
            symbol="WIPRO",
            client=client,
            api_key="key",
            access_token="token",
        )
        assert call_count["value"] == 2

    @pytest.mark.asyncio
    async def test_cache_hit_same_day(self) -> None:
        """Test that cache is not refreshed on same IST day."""
        cache = InstrumentsCache()
        call_count = {"value": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            if "/instruments/NSE" in req.url.path:
                call_count["value"] += 1
                return httpx.Response(200, text=_build_instruments_csv())
            return httpx.Response(404)

        client = KiteClient(http=_make_mock_client(handler))

        # First fetch
        await cache.get_token(
            tenant_id="tenant1",
            symbol="RELIANCE",
            client=client,
            api_key="key",
            access_token="token",
        )
        assert call_count["value"] == 1

        # Set cache to earlier today (but same date)
        key = ("tenant1", "NSE")
        if key in cache._cache:
            symbol_to_token, _fetched_at = cache._cache[key]
            earlier_today = datetime.now(IST) - timedelta(hours=2)
            cache._cache[key] = (symbol_to_token, earlier_today)

        # Next lookup should NOT trigger refresh
        await cache.get_token(
            tenant_id="tenant1",
            symbol="WIPRO",
            client=client,
            api_key="key",
            access_token="token",
        )
        assert call_count["value"] == 1


class TestConcurrency:
    """Test concurrent access and thundering herd prevention."""

    @pytest.mark.asyncio
    async def test_concurrent_lookups_single_fetch(self) -> None:
        """Test that concurrent lookups for same (tenant, exchange) result in single fetch."""
        import asyncio

        cache = InstrumentsCache()
        call_count = {"value": 0}

        async def slow_handler(req: httpx.Request) -> httpx.Response:
            if "/instruments/NSE" in req.url.path:
                call_count["value"] += 1
                await asyncio.sleep(0.1)  # Simulate slow fetch
                return httpx.Response(200, text=_build_instruments_csv())
            return httpx.Response(404)

        # Create client with async handler (need to patch)
        class SlowMockTransport(httpx.BaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                return await slow_handler(request)

            def handle_request(self, request: httpx.Request) -> httpx.Response:
                raise NotImplementedError("use async")

        client = KiteClient(http=httpx.AsyncClient(transport=SlowMockTransport()))

        # Launch concurrent lookups
        results = await asyncio.gather(
            cache.get_token(
                tenant_id="tenant1",
                symbol="RELIANCE",
                client=client,
                api_key="key",
                access_token="token",
            ),
            cache.get_token(
                tenant_id="tenant1",
                symbol="WIPRO",
                client=client,
                api_key="key",
                access_token="token",
            ),
            cache.get_token(
                tenant_id="tenant1",
                symbol="INFY",
                client=client,
                api_key="key",
                access_token="token",
            ),
        )

        # All should succeed
        assert results == [100000, 100001, 100002]
        # Only one fetch due to lock
        assert call_count["value"] == 1


class TestKiteClientGetInstruments:
    """Test KiteClient.get_instruments method."""

    @pytest.mark.asyncio
    async def test_get_instruments_csv_parsing(self) -> None:
        """Test that CSV parsing works correctly."""

        def handler(req: httpx.Request) -> httpx.Response:
            if "/instruments/NSE" in req.url.path:
                return httpx.Response(200, text=_build_instruments_csv())
            return httpx.Response(404)

        client = KiteClient(http=_make_mock_client(handler))

        instruments = await client.get_instruments(api_key="key", access_token="token")

        assert len(instruments) == 4
        assert instruments[0]["instrument_token"] == 100000
        assert instruments[0]["tradingsymbol"] == "RELIANCE"
        assert isinstance(instruments[0]["instrument_token"], int)

    @pytest.mark.asyncio
    async def test_get_instruments_different_exchange(self) -> None:
        """Test fetching instruments for different exchanges."""

        def handler(req: httpx.Request) -> httpx.Response:
            if "/instruments/BSE" in req.url.path:
                return httpx.Response(200, text=_build_instruments_csv(["BSE1", "BSE2"]))
            return httpx.Response(404)

        client = KiteClient(http=_make_mock_client(handler))

        instruments = await client.get_instruments(
            api_key="key", access_token="token", exchange="BSE"
        )

        assert len(instruments) == 2
        assert instruments[0]["tradingsymbol"] == "BSE1"
