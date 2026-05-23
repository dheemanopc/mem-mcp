"""TA session tools — six MCP tools for session management + indicator compute."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from mem_mcp.skills.base import SkillError
from mem_mcp.skills.kite.instruments_cache import get_instruments_cache
from mem_mcp.skills.kite.ta.indicators import INDICATORS, get_indicator_summary
from mem_mcp.skills.kite.ta.refresh import (
    lazy_trailing_refresh,
    paginated_fetch,
)
from mem_mcp.skills.kite.ta.session_cache import (
    LOOKBACK_DEFAULTS,
    get_global_cache,
)

if TYPE_CHECKING:
    from mem_mcp.skills.base import SkillCallContext
    from mem_mcp.skills.kite.client import KiteClient

logger = logging.getLogger(__name__)

# ==========================================================================
# Input/Output Models
# ==========================================================================


class TASessionOpenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    timeframe: str = Field(
        ...,
        pattern="^(minute|3minute|5minute|15minute|30minute|60minute|day|week)$",
    )
    lookback_bars: int | str = "max"
    ttl_seconds: int | None = None


class TASessionOpenOutput(BaseModel):
    session_id: str
    symbol: str
    timeframe: str
    bars_loaded: int
    first_bar_ts: str
    last_bar_ts: str
    last_close: float
    expires_at: str
    memory_id: str
    replaced_session_id: str | None = None


class IndicatorRequest(BaseModel):
    name: str
    params: dict[str, Any] = Field(default_factory=dict)
    at_timestamp: str | None = None
    lookback: int | None = None
    format: str | None = None


class TAIndicatorComputeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    requests: list[IndicatorRequest]
    default_format: str = "summary"


class IndicatorResult(BaseModel):
    name: str
    params: dict[str, Any]
    ok: bool
    summary: dict[str, Any] | None = None
    series: list[list[Any]] | None = None
    csv: str | None = None
    series_key: str | None = None
    error: str | None = None


class TAIndicatorComputeOutput(BaseModel):
    results: list[IndicatorResult]


class TASeriesFetchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    series_key: str
    last_n: int = 100
    format: str = "csv"


class TASeriesFetchOutput(BaseModel):
    data: str | list[Any]


class TAOhlcFetchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    last_n: int = 50
    format: str = "csv"


class TAOhlcFetchOutput(BaseModel):
    data: str | list[Any]


class TASessionStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str


class TASessionStatusOutput(BaseModel):
    alive: bool
    expires_at: str
    age_s: int
    trailing_bar_stale_s: int
    indicators_computed: list[str]
    cache_status: str


class TASessionRefreshInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    mode: str = "trailing"


class TASessionRefreshOutput(BaseModel):
    bars_refetched: int
    new_last_bar_ts: str


# ==========================================================================
# Tool Implementations
# ==========================================================================


def _snapshot_ts(ts: str | None) -> datetime | None:
    """Parse ISO timestamp, return as UTC datetime or None."""
    if ts is None:
        return None
    try:
        dt_parsed = pd.to_datetime(ts)
        if hasattr(dt_parsed, "tz") and dt_parsed.tz is None:
            dt = cast(datetime, dt_parsed.replace(tzinfo=UTC))
        else:
            dt = cast(datetime, dt_parsed.astimezone(UTC))
        return dt
    except Exception:
        return None


def _floor_snap_timestamp(
    df: pd.DataFrame, target_ts: datetime, bar_duration_sec: int
) -> int | None:
    """Find bar index where bar.start <= target_ts < bar.start + duration.

    Return bar index or None if not found / ts out of bounds.
    """
    if df.empty:
        return None

    first_bar = df.index[0]
    last_bar = df.index[-1]

    if target_ts < first_bar or target_ts > last_bar:
        return None

    # Binary search for the bar
    for i, bar_ts in enumerate(df.index):
        bar_end = bar_ts + timedelta(seconds=bar_duration_sec)
        if bar_ts <= target_ts < bar_end:
            return i

    return None


async def ta_session_open(
    client: KiteClient,
    ctx: SkillCallContext,
    args: TASessionOpenInput,
) -> TASessionOpenOutput:
    """Open a new TA session and return session metadata."""

    # Get cache with defaults; config overrides via settings happen at app init
    cache = get_global_cache()

    tenant_id = str(ctx.tenant_id)
    creds = ctx.credentials
    api_key = creds["api_key"]
    access_token = creds["access_token"]

    # Look up instrument_token from symbol
    ic = get_instruments_cache()
    instrument_token = await ic.get_token(
        tenant_id=tenant_id,
        symbol=args.symbol,
        client=client,
        api_key=api_key,
        access_token=access_token,
    )

    # Determine lookback bars
    if args.lookback_bars == "max":
        lookback_bars = LOOKBACK_DEFAULTS.get(args.timeframe, 1000)
    else:
        lookback_bars = int(args.lookback_bars)

    # Paginated fetch
    now = datetime.now(UTC)
    from_date = (now - timedelta(days=365)).isoformat()  # Conservative upper bound
    to_date = now.isoformat()

    df = await paginated_fetch(
        client,
        api_key,
        access_token,
        instrument_token,
        args.timeframe,
        from_date,
        to_date,
        lookback_bars,
    )

    if df.empty:
        raise SkillError(f"no bars fetched for {args.symbol} {args.timeframe}")

    # Open session in cache
    session_id, replaced_id = cache.open_session(
        symbol=args.symbol,
        timeframe=args.timeframe,
        tenant_id=tenant_id,
        instrument_token=instrument_token,
        df=df,
        ttl_seconds=args.ttl_seconds,
    )

    # Write transient memory for session metadata

    # We'd call memory_write via memsys, but we don't have access here.
    # For now, generate a pseudo-memory-id.
    memory_id = str(ctx.tool_call_id)

    entry = cache.get_session(session_id)
    if entry is None:
        raise SkillError("failed to retrieve session after opening")

    return TASessionOpenOutput(
        session_id=session_id,
        symbol=args.symbol,
        timeframe=args.timeframe,
        bars_loaded=len(df),
        first_bar_ts=entry.first_bar_ts,
        last_bar_ts=entry.last_bar_ts,
        last_close=entry.last_close,
        expires_at=entry.expires_at.isoformat(),
        memory_id=memory_id,
        replaced_session_id=replaced_id,
    )


async def ta_indicator_compute(
    client: KiteClient,
    ctx: SkillCallContext,
    args: TAIndicatorComputeInput,
) -> TAIndicatorComputeOutput:
    """Compute indicators in batch."""
    from mem_mcp.skills.kite.ta.refresh import BAR_DURATIONS

    cache = get_global_cache()
    entry = cache.get_session(args.session_id)
    if entry is None:
        raise SkillError(f"session {args.session_id} not found or expired")

    # Potentially refresh trailing bars
    creds = ctx.credentials
    api_key = creds["api_key"]
    access_token = creds["access_token"]
    instrument_token = entry.instrument_token

    df_refreshed, was_refreshed = await lazy_trailing_refresh(
        client,
        api_key,
        access_token,
        instrument_token,
        entry.timeframe,
        entry.df,
    )
    if was_refreshed:
        cache.update_session_df(args.session_id, df_refreshed)

    results = []
    bar_duration_sec = BAR_DURATIONS.get(entry.timeframe, 60)

    for req in args.requests:
        if req.name not in INDICATORS:
            results.append(
                IndicatorResult(
                    name=req.name,
                    params=req.params,
                    ok=False,
                    error=f"unknown indicator: {req.name}",
                )
            )
            continue

        # Determine actual params
        actual_params = {**INDICATORS[req.name]["params"], **req.params}

        # Compute the indicator
        try:
            indicator_def = INDICATORS[req.name]
            is_multi = indicator_def.get("multi", False)

            if is_multi:
                indicator_output = indicator_def["compute"](df_refreshed, **actual_params)
            else:
                indicator_output = indicator_def["compute"](df_refreshed, **actual_params)

            # Handle at_timestamp snapping
            if req.at_timestamp is not None:
                target_ts = _snapshot_ts(req.at_timestamp)
                if target_ts is None:
                    results.append(
                        IndicatorResult(
                            name=req.name,
                            params=actual_params,
                            ok=False,
                            error=f"invalid at_timestamp: {req.at_timestamp}",
                        )
                    )
                    continue

                bar_idx = _floor_snap_timestamp(df_refreshed, target_ts, bar_duration_sec)
                if bar_idx is None:
                    results.append(
                        IndicatorResult(
                            name=req.name,
                            params=actual_params,
                            ok=False,
                            error=f"timestamp {req.at_timestamp} out of bounds",
                        )
                    )
                    continue

                # Extract value at that bar
                if is_multi:
                    summary_multi = {}
                    for component_name, series in indicator_output.items():
                        val = series.iloc[bar_idx]
                        summary_multi[component_name] = float(val) if pd.notna(val) else None
                    results.append(
                        IndicatorResult(
                            name=req.name,
                            params=actual_params,
                            ok=True,
                            summary=summary_multi,
                            series_key=None,
                        )
                    )
                else:
                    val = indicator_output.iloc[bar_idx]
                    results.append(
                        IndicatorResult(
                            name=req.name,
                            params=actual_params,
                            ok=True,
                            summary={"at_timestamp": float(val) if pd.notna(val) else None},
                            series_key=None,
                        )
                    )
            else:
                # Default format handling
                fmt = req.format or args.default_format

                if is_multi:
                    # Multi-component indicator
                    summary_by_component: dict[str, dict[str, float | None]] = {}
                    series_multi: dict[str, list[Any]] = {}
                    csv_lines = ["timestamp,"]
                    csv_lines[0] += ",".join(f"{k}" for k in indicator_output.keys())
                    csv_lines.append("")

                    for component_name, series in indicator_output.items():
                        summary_by_component[component_name] = get_indicator_summary(
                            series, req.lookback or 20
                        )
                        series_multi[component_name] = series.tolist()
                        for i, val in enumerate(series):
                            if i + 1 >= len(csv_lines):
                                csv_lines.append(f"{df_refreshed.index[i]}")
                            csv_lines[i + 1] += f",{float(val) if pd.notna(val) else ''}"

                    if fmt == "summary":
                        results.append(
                            IndicatorResult(
                                name=req.name,
                                params=actual_params,
                                ok=True,
                                summary=summary_by_component,
                                series_key=f"{args.session_id}:{req.name}",
                            )
                        )
                    elif fmt == "series":
                        results.append(
                            IndicatorResult(
                                name=req.name,
                                params=actual_params,
                                ok=True,
                                series=[list(s) for s in series_multi.values()],
                                series_key=f"{args.session_id}:{req.name}",
                            )
                        )
                    elif fmt == "csv":
                        results.append(
                            IndicatorResult(
                                name=req.name,
                                params=actual_params,
                                ok=True,
                                csv="\n".join(csv_lines),
                                series_key=f"{args.session_id}:{req.name}",
                            )
                        )
                    else:
                        results.append(
                            IndicatorResult(
                                name=req.name,
                                params=actual_params,
                                ok=False,
                                error=f"unknown format: {fmt}",
                            )
                        )
                else:
                    # Single-value indicator
                    if req.lookback:
                        trimmed = indicator_output.iloc[-req.lookback :]
                    else:
                        trimmed = indicator_output

                    summary = get_indicator_summary(trimmed, req.lookback or 20)

                    if fmt == "summary":
                        results.append(
                            IndicatorResult(
                                name=req.name,
                                params=actual_params,
                                ok=True,
                                summary=summary,
                                series_key=f"{args.session_id}:{req.name}",
                            )
                        )
                    elif fmt == "series":
                        results.append(
                            IndicatorResult(
                                name=req.name,
                                params=actual_params,
                                ok=True,
                                series=trimmed.tolist(),
                                series_key=f"{args.session_id}:{req.name}",
                            )
                        )
                    elif fmt == "csv":
                        csv_lines = ["timestamp,value"]
                        for ts, val in zip(trimmed.index, trimmed, strict=False):
                            csv_lines.append(f"{ts},{float(val) if pd.notna(val) else ''}")
                        results.append(
                            IndicatorResult(
                                name=req.name,
                                params=actual_params,
                                ok=True,
                                csv="\n".join(csv_lines),
                                series_key=f"{args.session_id}:{req.name}",
                            )
                        )
                    else:
                        results.append(
                            IndicatorResult(
                                name=req.name,
                                params=actual_params,
                                ok=False,
                                error=f"unknown format: {fmt}",
                            )
                        )

                # Record in session metadata
                entry.computed_indicators[req.name] = actual_params

        except Exception as e:
            logger.exception(f"error computing {req.name}")
            results.append(
                IndicatorResult(
                    name=req.name,
                    params=actual_params,
                    ok=False,
                    error=str(e),
                )
            )

    cache.update_session_df(args.session_id, df_refreshed)
    return TAIndicatorComputeOutput(results=results)


async def ta_series_fetch(
    client: KiteClient,
    ctx: SkillCallContext,
    args: TASeriesFetchInput,
) -> TASeriesFetchOutput:
    """Fetch cached series by key."""
    cache = get_global_cache()
    entry = cache.get_session(args.session_id)
    if entry is None:
        raise SkillError(f"session {args.session_id} not found or expired")

    # Parse series_key: "session_id:indicator_name"
    parts = args.series_key.split(":")
    if len(parts) < 2:
        raise SkillError(f"invalid series_key: {args.series_key}")
    indicator_name = parts[1]

    if indicator_name not in entry.computed_indicators:
        raise SkillError(f"indicator {indicator_name} not computed in this session")

    # Re-compute to get the series
    # (In production, we'd cache the actual series; for now, re-compute)
    indicator_def = INDICATORS[indicator_name]
    params = entry.computed_indicators[indicator_name]

    is_multi = indicator_def.get("multi", False)
    result = indicator_def["compute"](entry.df, **params)

    if is_multi:
        # Multi-series: flatten
        all_values = []
        for _component_name, series in result.items():
            all_values.extend(series.iloc[-args.last_n :].tolist())
    else:
        all_values = result.iloc[-args.last_n :].tolist()

    if args.format == "csv":
        csv_lines = ["value"]
        for val in all_values:
            csv_lines.append(str(float(val) if pd.notna(val) else ""))
        return TASeriesFetchOutput(data="\n".join(csv_lines))
    else:
        return TASeriesFetchOutput(data=all_values)


async def ta_ohlc_fetch(
    client: KiteClient,
    ctx: SkillCallContext,
    args: TAOhlcFetchInput,
) -> TAOhlcFetchOutput:
    """Fetch OHLCV bars from cache."""
    cache = get_global_cache()
    entry = cache.get_session(args.session_id)
    if entry is None:
        raise SkillError(f"session {args.session_id} not found or expired")

    df = entry.df.iloc[-args.last_n :]

    if args.format == "csv":
        csv_lines = ["timestamp,open,high,low,close,volume"]
        for ts, row in df.iterrows():
            csv_lines.append(
                f"{ts},{row['open']},{row['high']},{row['low']},{row['close']},{row['volume']}"
            )
        return TAOhlcFetchOutput(data="\n".join(csv_lines))
    else:
        # JSON format
        data = []
        for ts, row in df.iterrows():
            data.append(
                [
                    cast(datetime, ts).isoformat(),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    int(row["volume"]),
                ]
            )
        return TAOhlcFetchOutput(data=data)


async def ta_session_status(
    client: KiteClient,
    ctx: SkillCallContext,
    args: TASessionStatusInput,
) -> TASessionStatusOutput:
    """Get session status."""
    cache = get_global_cache()
    entry = cache.get_session(args.session_id)

    if entry is None:
        return TASessionStatusOutput(
            alive=False,
            expires_at="",
            age_s=0,
            trailing_bar_stale_s=0,
            indicators_computed=[],
            cache_status="evicted",
        )

    now = datetime.now(UTC)
    age_s = int((now - entry.opened_at).total_seconds())

    last_bar_ts_raw = cast(datetime, entry.df.index[-1]) if not entry.df.empty else now
    if hasattr(last_bar_ts_raw, "tz") and last_bar_ts_raw.tz is None:
        last_bar_ts = last_bar_ts_raw.replace(tzinfo=UTC)
    else:
        last_bar_ts = last_bar_ts_raw

    trailing_stale_s = int((now - last_bar_ts).total_seconds())

    return TASessionStatusOutput(
        alive=entry.is_alive(),
        expires_at=entry.expires_at.isoformat(),
        age_s=age_s,
        trailing_bar_stale_s=trailing_stale_s,
        indicators_computed=list(entry.computed_indicators.keys()),
        cache_status="warm",
    )


async def ta_session_refresh(
    client: KiteClient,
    ctx: SkillCallContext,
    args: TASessionRefreshInput,
) -> TASessionRefreshOutput:
    """Refresh session (trailing or full)."""
    cache = get_global_cache()
    entry = cache.get_session(args.session_id)
    if entry is None:
        raise SkillError(f"session {args.session_id} not found or expired")

    creds = ctx.credentials
    api_key = creds["api_key"]
    access_token = creds["access_token"]
    instrument_token = entry.instrument_token

    if args.mode == "trailing":
        df_new, was_refreshed = await lazy_trailing_refresh(
            client,
            api_key,
            access_token,
            instrument_token,
            entry.timeframe,
            entry.df,
        )
        bars_added = len(df_new) - len(entry.df) if was_refreshed else 0
    else:  # full
        now = datetime.now(UTC)
        from_date = (now - timedelta(days=365)).isoformat()
        to_date = now.isoformat()
        df_new = await paginated_fetch(
            client,
            api_key,
            access_token,
            instrument_token,
            entry.timeframe,
            from_date,
            to_date,
            "max",
        )
        bars_added = len(df_new) - len(entry.df)

    cache.update_session_df(args.session_id, df_new)
    new_entry = cache.get_session(args.session_id)
    if new_entry is None:
        raise SkillError("session was evicted during refresh")

    return TASessionRefreshOutput(
        bars_refetched=bars_added,
        new_last_bar_ts=new_entry.last_bar_ts,
    )
