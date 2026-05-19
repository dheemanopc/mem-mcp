"""TA indicator implementations using pandas-ta."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pandas_ta


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute RSI (Relative Strength Index)."""
    result = pandas_ta.rsi(df["close"], length=period)
    return pd.Series(result, index=df.index)


def compute_ema(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Compute EMA (Exponential Moving Average)."""
    result = pandas_ta.ema(df["close"], length=period)
    return pd.Series(result, index=df.index)


def compute_sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Compute SMA (Simple Moving Average)."""
    result = pandas_ta.sma(df["close"], length=period)
    return pd.Series(result, index=df.index)


def compute_macd(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> dict[str, pd.Series]:
    """Compute MACD (Moving Average Convergence Divergence).

    Returns dict with keys 'macd', 'signal', 'histogram'.
    """
    macd_result = pandas_ta.macd(df["close"], fast=fast, slow=slow, signal=signal)
    # pandas_ta.macd returns: MACD, Histogram, Signal
    # Columns: MACD_12_26_9, MACDh_12_26_9, MACDs_12_26_9 (depending on params)
    result = {}
    if len(macd_result.columns) >= 3:
        result["macd"] = macd_result.iloc[:, 0]
        result["histogram"] = macd_result.iloc[:, 1]
        result["signal"] = macd_result.iloc[:, 2]
    else:
        # Fallback if structure differs
        result["macd"] = pd.Series(0.0, index=df.index)
        result["signal"] = pd.Series(0.0, index=df.index)
        result["histogram"] = pd.Series(0.0, index=df.index)
    return result


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute ATR (Average True Range)."""
    result = pandas_ta.atr(high=df["high"], low=df["low"], close=df["close"], length=period)
    return pd.Series(result, index=df.index)


def compute_bollinger(
    df: pd.DataFrame, period: int = 20, stddev: float = 2.0
) -> dict[str, pd.Series]:
    """Compute Bollinger Bands.

    Returns dict with keys 'upper', 'middle', 'lower'.
    """
    bb_result = pandas_ta.bbands(df["close"], length=period, lower_std=stddev, upper_std=stddev)
    result = {}
    if len(bb_result.columns) >= 3:
        result["upper"] = bb_result.iloc[:, 0]
        result["middle"] = bb_result.iloc[:, 1]
        result["lower"] = bb_result.iloc[:, 2]
    else:
        result["upper"] = pd.Series(0.0, index=df.index)
        result["middle"] = pd.Series(0.0, index=df.index)
        result["lower"] = pd.Series(0.0, index=df.index)
    return result


def compute_stoch(
    df: pd.DataFrame, k: int = 14, d: int = 3, smooth_k: int = 3
) -> dict[str, pd.Series]:
    """Compute Stochastic Oscillator.

    Returns dict with keys 'k', 'd'.
    """
    stoch_result = pandas_ta.stoch(
        high=df["high"], low=df["low"], close=df["close"], k=k, d=d, smooth_k=smooth_k
    )
    result = {}
    if len(stoch_result.columns) >= 2:
        result["k"] = stoch_result.iloc[:, 0]
        result["d"] = stoch_result.iloc[:, 1]
    else:
        result["k"] = pd.Series(0.0, index=df.index)
        result["d"] = pd.Series(0.0, index=df.index)
    return result


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute ADX (Average Directional Index)."""
    result = pandas_ta.adx(high=df["high"], low=df["low"], close=df["close"], length=period)
    # pandas_ta.adx returns multiple columns; get the ADX column (usually the first)
    if isinstance(result, pd.DataFrame) and len(result.columns) > 0:
        return result.iloc[:, 0]
    return pd.Series(0.0, index=df.index)


def compute_supertrend(
    df: pd.DataFrame, period: int = 10, multiplier: float = 3.0
) -> dict[str, pd.Series]:
    """Compute SuperTrend.

    Returns dict with keys 'supertrend', 'direction'.
    """
    st_result = pandas_ta.supertrend(
        high=df["high"], low=df["low"], close=df["close"], length=period, multiplier=multiplier
    )
    result = {}
    if isinstance(st_result, pd.DataFrame) and len(st_result.columns) >= 2:
        result["supertrend"] = st_result.iloc[:, 0]
        result["direction"] = st_result.iloc[:, 1]
    else:
        result["supertrend"] = pd.Series(0.0, index=df.index)
        result["direction"] = pd.Series(0.0, index=df.index)
    return result


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    """Compute VWAP (Volume-Weighted Average Price).

    Session anchor: from start of trading day (IST).
    """
    # Reset VWAP daily (IST anchor)
    result = pandas_ta.vwap(high=df["high"], low=df["low"], close=df["close"], volume=df["volume"])
    return pd.Series(result, index=df.index)


def compute_obv(df: pd.DataFrame) -> pd.Series:
    """Compute OBV (On-Balance Volume)."""
    result = pandas_ta.obv(close=df["close"], volume=df["volume"])
    return pd.Series(result, index=df.index)


def compute_mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute MFI (Money Flow Index)."""
    result = pandas_ta.mfi(
        high=df["high"], low=df["low"], close=df["close"], volume=df["volume"], length=period
    )
    return pd.Series(result, index=df.index)


def compute_cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Compute CCI (Commodity Channel Index)."""
    result = pandas_ta.cci(high=df["high"], low=df["low"], close=df["close"], length=period)
    return pd.Series(result, index=df.index)


def compute_williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Williams %R."""
    result = pandas_ta.willr(high=df["high"], low=df["low"], close=df["close"], length=period)
    return pd.Series(result, index=df.index)


# Indicator registry
INDICATORS: dict[str, dict[str, Any]] = {
    "rsi": {
        "compute": compute_rsi,
        "params": {"period": 14},
        "multi": False,
    },
    "ema": {
        "compute": compute_ema,
        "params": {"period": 20},
        "multi": False,
    },
    "sma": {
        "compute": compute_sma,
        "params": {"period": 20},
        "multi": False,
    },
    "macd": {
        "compute": compute_macd,
        "params": {"fast": 12, "slow": 26, "signal": 9},
        "multi": True,
        "components": ["macd", "signal", "histogram"],
    },
    "atr": {
        "compute": compute_atr,
        "params": {"period": 14},
        "multi": False,
    },
    "bollinger": {
        "compute": compute_bollinger,
        "params": {"period": 20, "stddev": 2.0},
        "multi": True,
        "components": ["upper", "middle", "lower"],
    },
    "stoch": {
        "compute": compute_stoch,
        "params": {"k": 14, "d": 3, "smooth_k": 3},
        "multi": True,
        "components": ["k", "d"],
    },
    "adx": {
        "compute": compute_adx,
        "params": {"period": 14},
        "multi": False,
    },
    "supertrend": {
        "compute": compute_supertrend,
        "params": {"period": 10, "multiplier": 3.0},
        "multi": True,
        "components": ["supertrend", "direction"],
    },
    "vwap": {
        "compute": compute_vwap,
        "params": {},
        "multi": False,
    },
    "obv": {
        "compute": compute_obv,
        "params": {},
        "multi": False,
    },
    "mfi": {
        "compute": compute_mfi,
        "params": {"period": 14},
        "multi": False,
    },
    "cci": {
        "compute": compute_cci,
        "params": {"period": 20},
        "multi": False,
    },
    "williams_r": {
        "compute": compute_williams_r,
        "params": {"period": 14},
        "multi": False,
    },
}


def get_indicator_summary(series: pd.Series, lookback: int = 20) -> dict[str, float | None]:
    """Compute summary stats for a single series.

    Returns: {last, prev, min_N, max_N} where N=lookback.
    """
    summary = {}
    if len(series) > 0:
        summary["last"] = float(series.iloc[-1]) if pd.notna(series.iloc[-1]) else None
    if len(series) > 1:
        summary["prev"] = float(series.iloc[-2]) if pd.notna(series.iloc[-2]) else None
    if len(series) > 0:
        recent = series.iloc[-lookback:].dropna()
        if len(recent) > 0:
            summary[f"min_{lookback}"] = float(recent.min())
            summary[f"max_{lookback}"] = float(recent.max())
    return summary
