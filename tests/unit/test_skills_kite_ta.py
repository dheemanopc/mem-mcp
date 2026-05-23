"""Unit tests for Kite TA sessions + indicators."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pandas as pd
import pytest

from mem_mcp.skills.base import SkillCallContext
from mem_mcp.skills.kite.client import KiteClient
from mem_mcp.skills.kite.instruments_cache import get_instruments_cache
from mem_mcp.skills.kite.ta import (
    TAIndicatorComputeInput,
    TAOhlcFetchInput,
    TASeriesFetchInput,
    TASessionOpenInput,
    TASessionStatusInput,
    reset_cache_for_tests,
    ta_indicator_compute,
    ta_ohlc_fetch,
    ta_series_fetch,
    ta_session_open,
    ta_session_status,
)
from mem_mcp.skills.kite.ta.session_cache import get_global_cache
from mem_mcp.skills.kite.ta.tools import IndicatorRequest

# ==========================================================================
# Helpers
# ==========================================================================


def _make_mock_client(handler: Any) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient backed by a MockTransport."""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


def _build_skill_ctx(creds: dict[str, Any] | None = None) -> SkillCallContext:
    """Build a skill context for testing."""
    return SkillCallContext(
        tenant_id=uuid4(),
        identity_id=uuid4(),
        client_id="test-client",
        request_id=str(uuid4()),
        credentials=creds
        if creds is not None
        else {
            "api_key": "AKEY",
            "api_secret": "ASEC",
            "access_token": "ATOK",
        },
        tool_call_id=uuid4(),
    )


def _build_sample_candles(num_bars: int = 50) -> list[list[Any]]:
    """Build sample OHLCV candles for testing."""
    candles = []
    base_price = 100.0
    now = datetime.now(UTC)

    for i in range(num_bars):
        ts = now - pd.Timedelta(minutes=15 * (num_bars - i - 1))
        open_p = base_price + i * 0.1
        close_p = open_p + (i % 5) * 0.05
        high_p = max(open_p, close_p) + 0.1
        low_p = min(open_p, close_p) - 0.1
        volume = 1000000 + i * 10000

        candles.append(
            [
                ts.isoformat(),
                open_p,
                high_p,
                low_p,
                close_p,
                volume,
            ]
        )

    return candles


def _build_instruments_csv(symbols: list[str] | None = None) -> str:
    """Build sample instruments CSV for testing."""
    if symbols is None:
        symbols = ["WIPRO", "RELIANCE", "STOCK0", "STOCK1", "STOCK2", "STOCK3"]

    lines = [
        "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange"
    ]
    for i, symbol in enumerate(symbols):
        token = 123456 + i
        lines.append(f"{token},0,{symbol},{symbol} LTD,100.0,,0,0.05,1,CE,EQUITY,NSE")

    return "\n".join(lines)


def _make_standard_handler(extra_handler: Any = None) -> Any:
    """Create a handler that handles /instruments/{exchange} and /instruments/historical."""

    def handler(req: httpx.Request) -> httpx.Response:
        if "/instruments/NSE" in req.url.path:
            # Return CSV for instruments master
            return httpx.Response(200, text=_build_instruments_csv())
        elif "/instruments/historical/" in req.url.path:
            # Try extra_handler first for custom candles
            if extra_handler:
                result = extra_handler(req)
                if result.status_code != 404:
                    return result
            # Default: return sample candles
            candles = _build_sample_candles(50)
            return httpx.Response(200, json={"status": "success", "data": candles})
        elif extra_handler:
            return extra_handler(req)
        return httpx.Response(404)

    return handler


# ==========================================================================
# Tests
# ==========================================================================


class TestTASessionLifecycle:
    """Test session open, status, close lifecycle."""

    @pytest.mark.asyncio
    async def test_session_open_success(self) -> None:
        """Test opening a TA session."""
        reset_cache_for_tests()

        client = KiteClient(http=_make_mock_client(_make_standard_handler()))
        ctx = _build_skill_ctx()

        args = TASessionOpenInput(symbol="WIPRO", timeframe="15minute", lookback_bars=50)
        result = await ta_session_open(client, ctx, args)

        assert result.session_id
        assert result.symbol == "WIPRO"
        assert result.timeframe == "15minute"
        assert result.bars_loaded == 50
        assert result.first_bar_ts
        assert result.last_bar_ts
        assert result.last_close > 0
        assert result.expires_at
        assert result.memory_id
        assert result.replaced_session_id is None

    @pytest.mark.asyncio
    async def test_session_status_alive(self) -> None:
        """Test checking session status."""
        reset_cache_for_tests()

        client = KiteClient(http=_make_mock_client(_make_standard_handler()))
        ctx = _build_skill_ctx()

        # Open session
        open_args = TASessionOpenInput(symbol="WIPRO", timeframe="15minute")
        open_result = await ta_session_open(client, ctx, open_args)
        session_id = open_result.session_id

        # Check status
        status_args = TASessionStatusInput(session_id=session_id)
        status_result = await ta_session_status(client, ctx, status_args)

        assert status_result.alive is True
        assert status_result.age_s >= 0
        assert status_result.trailing_bar_stale_s >= 0
        assert status_result.indicators_computed == []
        assert status_result.cache_status == "warm"

    @pytest.mark.asyncio
    async def test_session_expired(self) -> None:
        """Test that expired sessions are marked as evicted."""
        reset_cache_for_tests()

        client = KiteClient(http=_make_mock_client(_make_standard_handler()))
        ctx = _build_skill_ctx()

        # Open with small TTL (1 second)
        open_args = TASessionOpenInput(symbol="WIPRO", timeframe="15minute", ttl_seconds=1)
        open_result = await ta_session_open(client, ctx, open_args)
        session_id = open_result.session_id

        # Manually expire the session by modifying its expires_at time
        cache = get_global_cache()
        entry = cache.get_session(session_id)
        if entry is not None:
            # Set expires_at to the past
            from datetime import timedelta as td

            entry.expires_at = datetime.now(UTC) - td(seconds=1)

        # Check status on expired session
        status_args = TASessionStatusInput(session_id=session_id)
        status_result = await ta_session_status(client, ctx, status_args)

        assert status_result.alive is False
        assert status_result.cache_status == "evicted"


class TestIndicatorCompute:
    """Test indicator batch compute."""

    @pytest.mark.asyncio
    async def test_indicator_compute_batch(self) -> None:
        """Test computing multiple indicators in batch."""
        reset_cache_for_tests()

        client = KiteClient(http=_make_mock_client(_make_standard_handler()))
        ctx = _build_skill_ctx()

        # Open session
        open_args = TASessionOpenInput(symbol="WIPRO", timeframe="15minute", lookback_bars=100)
        open_result = await ta_session_open(client, ctx, open_args)
        session_id = open_result.session_id

        # Compute indicators
        compute_args = TAIndicatorComputeInput(
            session_id=session_id,
            requests=[
                IndicatorRequest(name="rsi", params={"period": 14}),
                IndicatorRequest(name="ema", params={"period": 20}),
                IndicatorRequest(name="atr", params={"period": 14}),
            ],
            default_format="summary",
        )

        result = await ta_indicator_compute(client, ctx, compute_args)

        # Check results
        assert len(result.results) == 3
        for res in result.results:
            assert res.name in ["rsi", "ema", "atr"]
            assert res.ok is True
            assert res.summary is not None
            assert "last" in res.summary
            assert res.series_key is not None

    @pytest.mark.asyncio
    async def test_indicator_compute_unknown(self) -> None:
        """Test that unknown indicators are handled gracefully."""
        reset_cache_for_tests()

        client = KiteClient(http=_make_mock_client(_make_standard_handler()))
        ctx = _build_skill_ctx()

        # Open session
        open_args = TASessionOpenInput(symbol="WIPRO", timeframe="15minute")
        open_result = await ta_session_open(client, ctx, open_args)
        session_id = open_result.session_id

        # Compute with unknown + known
        compute_args = TAIndicatorComputeInput(
            session_id=session_id,
            requests=[
                IndicatorRequest(name="unknown_indicator", params={}),
                IndicatorRequest(name="rsi", params={"period": 14}),
            ],
        )

        result = await ta_indicator_compute(client, ctx, compute_args)

        assert len(result.results) == 2
        unknown_result = next((r for r in result.results if r.name == "unknown_indicator"), None)
        assert unknown_result is not None
        assert unknown_result.ok is False
        assert unknown_result.error is not None and "unknown indicator" in unknown_result.error

        rsi_result = next((r for r in result.results if r.name == "rsi"), None)
        assert rsi_result is not None
        assert rsi_result.ok is True


class TestOhlcFetch:
    """Test OHLC data fetch."""

    @pytest.mark.asyncio
    async def test_ohlc_fetch_csv(self) -> None:
        """Test fetching OHLCV as CSV."""
        reset_cache_for_tests()

        client = KiteClient(http=_make_mock_client(_make_standard_handler()))
        ctx = _build_skill_ctx()

        # Open session
        open_args = TASessionOpenInput(symbol="WIPRO", timeframe="15minute")
        open_result = await ta_session_open(client, ctx, open_args)
        session_id = open_result.session_id

        # Fetch OHLC
        fetch_args = TAOhlcFetchInput(session_id=session_id, last_n=10, format="csv")
        result = await ta_ohlc_fetch(client, ctx, fetch_args)

        assert isinstance(result.data, str)
        lines = result.data.split("\n")
        assert lines[0].startswith("timestamp")
        assert len(lines) >= 10  # header + 10 bars


class TestSeriesFetch:
    """Test series data fetch."""

    @pytest.mark.asyncio
    async def test_series_fetch_json(self) -> None:
        """Test fetching series as JSON."""
        reset_cache_for_tests()

        client = KiteClient(http=_make_mock_client(_make_standard_handler()))
        ctx = _build_skill_ctx()

        # Open session
        open_args = TASessionOpenInput(symbol="WIPRO", timeframe="15minute")
        open_result = await ta_session_open(client, ctx, open_args)
        session_id = open_result.session_id

        # Compute RSI
        compute_args = TAIndicatorComputeInput(
            session_id=session_id,
            requests=[IndicatorRequest(name="rsi", params={"period": 14})],
        )
        compute_result = await ta_indicator_compute(client, ctx, compute_args)
        series_key = compute_result.results[0].series_key
        assert series_key is not None

        # Fetch series
        fetch_args = TASeriesFetchInput(
            session_id=session_id, series_key=series_key, last_n=50, format="json"
        )
        result = await ta_series_fetch(client, ctx, fetch_args)

        assert isinstance(result.data, list)
        assert len(result.data) <= 50


class TestPerTenantCap:
    """Test per-tenant session cap."""

    @pytest.mark.asyncio
    async def test_per_tenant_cap_eviction(self) -> None:
        """Test that oldest session is evicted when per-tenant cap reached."""
        reset_cache_for_tests()

        # Set a low cap for testing
        from mem_mcp.skills.kite.ta.session_cache import SessionCache

        cache = SessionCache(max_sessions=50, max_per_tenant=3)

        # Manually set the cache (bypass get_global_cache)
        import mem_mcp.skills.kite.ta.session_cache as cache_mod

        cache_mod._global_cache = cache

        client = KiteClient(http=_make_mock_client(_make_standard_handler()))
        tenant_id = uuid4()
        ctx = SkillCallContext(
            tenant_id=tenant_id,
            identity_id=uuid4(),
            client_id="test-client",
            request_id=str(uuid4()),
            credentials={"api_key": "AKEY", "api_secret": "ASEC", "access_token": "ATOK"},
            tool_call_id=uuid4(),
        )

        # Open 3 sessions (at cap)
        session_ids = []
        for i in range(3):
            open_args = TASessionOpenInput(
                symbol=f"STOCK{i}", timeframe="15minute", lookback_bars=30
            )
            result = await ta_session_open(client, ctx, open_args)
            session_ids.append(result.session_id)
            assert result.replaced_session_id is None

        # Open 4th (should evict 1st)
        open_args = TASessionOpenInput(symbol="STOCK3", timeframe="15minute", lookback_bars=30)
        result = await ta_session_open(client, ctx, open_args)
        assert result.replaced_session_id == session_ids[0]

        # Verify 1st is gone, others remain
        status_args = TASessionStatusInput(session_id=session_ids[0])
        status = await ta_session_status(client, ctx, status_args)
        assert status.alive is False


class TestFloorSnapping:
    """Test timestamp floor-snap semantics."""

    @pytest.mark.asyncio
    async def test_at_timestamp_snapping(self) -> None:
        """Test that at_timestamp snaps to the containing bar."""
        reset_cache_for_tests()

        # Build 100 bars starting at a known time
        candles = []
        base_time = datetime(2026, 5, 19, 9, 0, 0, tzinfo=UTC)
        for i in range(100):
            ts = base_time + pd.Timedelta(minutes=15 * i)
            open_p = 100.0 + i * 0.1
            close_p = open_p + 0.5
            candles.append([ts.isoformat(), open_p, open_p + 1, open_p - 1, close_p, 1000000])

        def custom_handler(req: httpx.Request) -> httpx.Response:
            if "/instruments/historical/" in req.url.path:
                return httpx.Response(200, json={"status": "success", "data": candles})
            return httpx.Response(404)

        client = KiteClient(http=_make_mock_client(_make_standard_handler(custom_handler)))
        ctx = _build_skill_ctx()

        # Open session
        open_args = TASessionOpenInput(symbol="WIPRO", timeframe="15minute")
        open_result = await ta_session_open(client, ctx, open_args)
        session_id = open_result.session_id

        # Request indicator at specific timestamp (mid-bar)
        # Bar starts at 09:15:00, so request 09:15:30 (within the bar)
        target_ts = (base_time + pd.Timedelta(minutes=15)).replace(second=30).isoformat()

        compute_args = TAIndicatorComputeInput(
            session_id=session_id,
            requests=[
                IndicatorRequest(
                    name="rsi", params={"period": 14}, at_timestamp=target_ts, format="summary"
                )
            ],
        )

        result = await ta_indicator_compute(client, ctx, compute_args)

        assert result.results[0].ok is True
        assert result.results[0].summary is not None
        assert "at_timestamp" in result.results[0].summary
