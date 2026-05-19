"""TA (Technical Analysis) submodule for Kite skill.

Phase 2 extension providing in-process session management + indicator compute.
"""

from mem_mcp.skills.kite.ta.session_cache import get_global_cache, reset_cache_for_tests
from mem_mcp.skills.kite.ta.tools import (
    TAIndicatorComputeInput,
    TAIndicatorComputeOutput,
    TAOhlcFetchInput,
    TAOhlcFetchOutput,
    TASeriesFetchInput,
    TASeriesFetchOutput,
    TASessionOpenInput,
    TASessionOpenOutput,
    TASessionRefreshInput,
    TASessionRefreshOutput,
    TASessionStatusInput,
    TASessionStatusOutput,
    ta_indicator_compute,
    ta_ohlc_fetch,
    ta_series_fetch,
    ta_session_open,
    ta_session_refresh,
    ta_session_status,
)

__all__ = [
    "get_global_cache",
    "reset_cache_for_tests",
    "TASessionOpenInput",
    "TASessionOpenOutput",
    "TAIndicatorComputeInput",
    "TAIndicatorComputeOutput",
    "TASeriesFetchInput",
    "TASeriesFetchOutput",
    "TAOhlcFetchInput",
    "TAOhlcFetchOutput",
    "TASessionStatusInput",
    "TASessionStatusOutput",
    "TASessionRefreshInput",
    "TASessionRefreshOutput",
    "ta_session_open",
    "ta_indicator_compute",
    "ta_series_fetch",
    "ta_ohlc_fetch",
    "ta_session_status",
    "ta_session_refresh",
]
