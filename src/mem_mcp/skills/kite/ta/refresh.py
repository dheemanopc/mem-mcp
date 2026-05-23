"""Lazy trailing-bar refresh logic for TA sessions.

Handles paginated historical fetches and trailing-bar updates.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import pandas as pd

from mem_mcp.skills.base import SkillError
from mem_mcp.skills.kite.client import KiteApiError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Bar duration in seconds per timeframe
BAR_DURATIONS: dict[str, int] = {
    "minute": 60,
    "3minute": 3 * 60,
    "5minute": 5 * 60,
    "15minute": 15 * 60,
    "30minute": 30 * 60,
    "60minute": 60 * 60,
    "day": 24 * 60 * 60,
    "week": 7 * 24 * 60 * 60,
}

# Max bars per Kite API call (conservative estimate)
KITE_MAX_BARS_PER_CALL = 1000


def parse_kite_candles(candles: list[list[Any]]) -> pd.DataFrame:
    """Convert Kite candle format [datetime, open, high, low, close, volume] to DataFrame.

    Index is timestamp (datetime), columns: open, high, low, close, volume.
    """
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    data = []
    for candle in candles:
        # candle: [timestamp_str, open, high, low, close, volume, ...]
        ts_str = candle[0]
        try:
            ts = pd.to_datetime(ts_str)
        except Exception:
            continue
        data.append(
            {
                "timestamp": ts,
                "open": candle[1],
                "high": candle[2],
                "low": candle[3],
                "close": candle[4],
                "volume": candle[5] if len(candle) > 5 else 0,
            }
        )

    df = pd.DataFrame(data)
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df.set_index("timestamp", inplace=True)
    # Ensure the index is timezone-aware (UTC)
    idx = cast(pd.DatetimeIndex, df.index)
    if idx.tz is None:
        df.index = idx.tz_localize("UTC")
    return df


async def paginated_fetch(
    client: Any,
    api_key: str,
    access_token: str,
    instrument_token: int,
    interval: str,
    from_date: str,
    to_date: str,
    lookback_bars: int | str = "max",
) -> pd.DataFrame:
    """Fetch historical data with pagination to handle Kite's per-call limits.

    Args:
        client: KiteClient instance
        api_key: Kite API key
        access_token: Kite access token
        instrument_token: Kite instrument token
        interval: timeframe (minute, 5minute, day, etc.)
        from_date: ISO string start date
        to_date: ISO string end date
        lookback_bars: max bars to fetch (or "max" for default)

    Returns:
        Concatenated DataFrame with all bars, indexed by timestamp.
    """
    from mem_mcp.skills.kite.ta.session_cache import LOOKBACK_DEFAULTS

    # Parse dates
    from_dt = pd.to_datetime(from_date)
    to_dt = pd.to_datetime(to_date)

    # Determine target bar count
    if lookback_bars == "max":
        target_bars = LOOKBACK_DEFAULTS.get(interval, 1000)
    else:
        target_bars = int(lookback_bars)

    # Estimate bar duration and walk backwards
    bar_duration_sec = BAR_DURATIONS.get(interval, 60)
    total_bars_needed = target_bars
    all_dfs: list[pd.DataFrame] = []

    # Walk backwards in chunks
    current_to = to_dt
    while total_bars_needed > 0:
        # Estimate date range for this chunk
        bars_this_chunk = min(total_bars_needed, KITE_MAX_BARS_PER_CALL)
        chunk_seconds = bars_this_chunk * bar_duration_sec
        current_from = current_to - timedelta(seconds=chunk_seconds)

        # Clamp to the requested from_date
        if current_from < from_dt:
            current_from = from_dt

        # Fetch (Kite requires yyyy-mm-dd HH:MM:SS format, not ISO-8601)
        try:
            result = await client.get_historical_data(
                api_key=api_key,
                access_token=access_token,
                instrument_token=instrument_token,
                interval=interval,
                from_date=current_from.strftime("%Y-%m-%d %H:%M:%S"),
                to_date=current_to.strftime("%Y-%m-%d %H:%M:%S"),
                continuous=False,
                oi=False,
            )
            candles = result if isinstance(result, list) else result.get("candles", [])
            if candles:
                df_chunk = parse_kite_candles(candles)
                all_dfs.insert(0, df_chunk)  # prepend (walking backwards)
                total_bars_needed -= len(candles)
            else:
                # No more bars upstream — we've reached the data limit
                break
        except KiteApiError as e:
            if not all_dfs:
                # First chunk failed — raise so the user sees the real error
                raise SkillError(
                    f"kite fetch failed for instrument={instrument_token} interval={interval}: "
                    f"{e.error_type or 'error'}: {e}"
                ) from e
            # After partial success, log and stop (better to return partial than break mid-fetch)
            logger.warning(f"kite fetch error after partial success: {e}")
            break

        # Stop if we hit the start boundary
        if current_from <= from_dt:
            break

        current_to = current_from

    if not all_dfs:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    # Concatenate and deduplicate
    df_all = pd.concat(all_dfs, ignore_index=False)
    df_all = df_all[~df_all.index.duplicated(keep="first")]
    df_all.sort_index(inplace=True)
    return df_all


async def lazy_trailing_refresh(
    client: Any,
    api_key: str,
    access_token: str,
    instrument_token: int,
    interval: str,
    current_df: pd.DataFrame,
) -> tuple[pd.DataFrame, bool]:
    """Refresh trailing bars if stale. Return (updated_df, was_refreshed).

    Args:
        client: KiteClient instance
        api_key: Kite API key
        access_token: Kite access token
        instrument_token: Kite instrument token
        interval: timeframe
        current_df: current cached DataFrame

    Returns:
        (updated_df, was_refreshed) — updated_df may be current_df if no refresh needed.
    """
    if current_df.empty:
        return current_df, False

    bar_duration = BAR_DURATIONS.get(interval, 60)
    now = datetime.now(UTC)
    last_bar_ts = current_df.index[-1]

    # Ensure both are timezone-aware
    if last_bar_ts.tz is None:
        last_bar_ts = last_bar_ts.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    time_since_last_bar = (now - last_bar_ts).total_seconds()

    # If last bar is fresh, no refresh needed
    if time_since_last_bar < bar_duration:
        return current_df, False

    # Refresh from last bar onwards (Kite requires yyyy-mm-dd HH:MM:SS format)
    refresh_from = last_bar_ts + timedelta(seconds=1)
    try:
        result = await client.get_historical_data(
            api_key=api_key,
            access_token=access_token,
            instrument_token=instrument_token,
            interval=interval,
            from_date=refresh_from.strftime("%Y-%m-%d %H:%M:%S"),
            to_date=now.strftime("%Y-%m-%d %H:%M:%S"),
            continuous=False,
            oi=False,
        )
        candles = result if isinstance(result, list) else result.get("candles", [])
        if candles:
            df_new = parse_kite_candles(candles)
            # Append new bars (remove overlaps with last_bar)
            df_new = df_new[df_new.index > last_bar_ts]
            if not df_new.empty:
                df_refreshed = pd.concat([current_df, df_new])
                df_refreshed = df_refreshed[~df_refreshed.index.duplicated(keep="first")]
                df_refreshed.sort_index(inplace=True)
                return df_refreshed, True
    except KiteApiError as e:
        logger.warning(f"kite trailing refresh error: {e}")

    return current_df, False
