"""In-process LRU cache for TA sessions.

Manages per-tenant sessions with sliding TTL by timeframe. Each session caches
OHLCV data + computed indicators.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pandas as pd

logger = logging.getLogger(__name__)

# Timeframe-based TTL defaults (seconds)
TIMEFRAME_TTLS: dict[str, int] = {
    "minute": 30 * 60,  # 30 min
    "3minute": 60 * 60,  # 1 hour
    "5minute": 60 * 60,  # 1 hour
    "15minute": 2 * 60 * 60,  # 2 hours
    "30minute": 2 * 60 * 60,  # 2 hours
    "60minute": 4 * 60 * 60,  # 4 hours
    "day": 12 * 60 * 60,  # 12 hours
    "week": 24 * 60 * 60,  # 24 hours
}

# Lookback bar defaults per timeframe
LOOKBACK_DEFAULTS: dict[str, int] = {
    "minute": 60 * 24,  # 60 days
    "3minute": 100 * 24 * (60 // 3),  # ~100 days of 3min bars
    "5minute": 100 * 24 * (60 // 5),  # ~100 days of 5min bars
    "15minute": 200 * 24 * (60 // 15),  # ~200 days
    "30minute": 200 * 24 * (60 // 30),  # ~200 days
    "60minute": 400 * 24,  # ~400 days
    "day": 2000,  # 2000 days
    "week": 1000,  # 1000 weeks
}


@dataclass
class SessionEntry:
    """In-memory session entry holding OHLCV data + metadata."""

    session_id: str
    tenant_id: str
    symbol: str
    timeframe: str
    instrument_token: int
    opened_at: datetime
    expires_at: datetime
    ttl_seconds: int
    df: pd.DataFrame  # index: datetime, cols: open, high, low, close, volume
    last_refresh_ts: datetime
    computed_indicators: dict[str, Any] = field(default_factory=dict)

    def is_alive(self) -> bool:
        """Check if session has not expired."""
        return datetime.now(UTC) < self.expires_at

    def refresh_ttl(self) -> None:
        """Slide the TTL forward by ttl_seconds from now."""
        self.expires_at = datetime.now(UTC).replace(microsecond=0) + pd.Timedelta(
            seconds=self.ttl_seconds
        )

    @property
    def first_bar_ts(self) -> str:
        """Return ISO-formatted timestamp of first bar."""
        if self.df.empty:
            return ""
        return cast(str, self.df.index[0].isoformat())

    @property
    def last_bar_ts(self) -> str:
        """Return ISO-formatted timestamp of last bar."""
        if self.df.empty:
            return ""
        return cast(str, self.df.index[-1].isoformat())

    @property
    def last_close(self) -> float:
        """Return the close price of the last bar."""
        if self.df.empty or "close" not in self.df.columns:
            return 0.0
        return float(self.df["close"].iloc[-1])


class SessionCache:
    """Thread-safe LRU cache for TA sessions.

    Per-tenant cap (default 10 active sessions). When opening beyond cap,
    evicts oldest session for that tenant (LRU by opened_at).
    """

    def __init__(self, max_sessions: int = 50, max_per_tenant: int = 10) -> None:
        self._max_sessions = max_sessions
        self._max_per_tenant = max_per_tenant
        self._lock = threading.RLock()
        # session_id -> SessionEntry
        self._sessions: dict[str, SessionEntry] = {}
        # tenant_id -> list of session_ids (LRU order: oldest first)
        self._tenant_sessions: dict[str, list[str]] = {}

    def open_session(
        self,
        symbol: str,
        timeframe: str,
        tenant_id: str,
        instrument_token: int,
        df: pd.DataFrame,
        ttl_seconds: int | None = None,
    ) -> tuple[str, str | None]:
        """Open a new session. Return (session_id, replaced_session_id or None).

        If per-tenant cap is reached, evict the oldest session for that tenant.
        """
        with self._lock:
            # Determine TTL
            if ttl_seconds is None:
                ttl_seconds = TIMEFRAME_TTLS.get(timeframe, 30 * 60)

            # Evict if needed
            replaced_id = None
            tenant_sessions = self._tenant_sessions.get(tenant_id, [])
            if len(tenant_sessions) >= self._max_per_tenant:
                # Remove oldest for this tenant
                removed_id = tenant_sessions.pop(0)
                del self._sessions[removed_id]
                replaced_id = removed_id
                logger.warning(
                    "evicted_old_ta_session",
                    extra={
                        "evicted_session_id": removed_id,
                        "tenant_id": tenant_id,
                    },
                )

            # Create new session
            session_id = str(uuid4())
            now = datetime.now(UTC).replace(microsecond=0)
            expires_at = now + pd.Timedelta(seconds=ttl_seconds)

            entry = SessionEntry(
                session_id=session_id,
                tenant_id=tenant_id,
                symbol=symbol,
                timeframe=timeframe,
                instrument_token=instrument_token,
                opened_at=now,
                expires_at=expires_at,
                ttl_seconds=ttl_seconds,
                df=df.copy(),
                last_refresh_ts=now,
            )

            self._sessions[session_id] = entry
            if tenant_id not in self._tenant_sessions:
                self._tenant_sessions[tenant_id] = []
            self._tenant_sessions[tenant_id].append(session_id)

            return session_id, replaced_id

    def get_session(self, session_id: str) -> SessionEntry | None:
        """Retrieve a session by ID. Return None if not found or expired."""
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                return None
            if not entry.is_alive():
                # Expired; evict
                self._sessions.pop(session_id, None)
                if entry.tenant_id in self._tenant_sessions:
                    self._tenant_sessions[entry.tenant_id].remove(session_id)
                return None
            return entry

    def update_session_df(self, session_id: str, df: pd.DataFrame) -> None:
        """Update a session's DataFrame (e.g., after refresh)."""
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is not None:
                entry.df = df.copy()
                entry.last_refresh_ts = datetime.now(UTC).replace(microsecond=0)
                entry.refresh_ttl()

    def close_session(self, session_id: str) -> bool:
        """Explicitly close a session. Return True if found."""
        with self._lock:
            entry = self._sessions.pop(session_id, None)
            if entry is not None:
                if entry.tenant_id in self._tenant_sessions:
                    self._tenant_sessions[entry.tenant_id].remove(session_id)
                return True
            return False

    def get_all_sessions_for_tenant(self, tenant_id: str) -> list[SessionEntry]:
        """Get all live sessions for a tenant."""
        with self._lock:
            session_ids = self._tenant_sessions.get(tenant_id, [])
            result = []
            for sid in session_ids:
                entry = self._sessions.get(sid)
                if entry is not None and entry.is_alive():
                    result.append(entry)
            return result

    def prune_expired(self) -> int:
        """Remove all expired sessions. Return count removed."""
        with self._lock:
            expired = [sid for sid, entry in self._sessions.items() if not entry.is_alive()]
            for sid in expired:
                self._sessions.pop(sid)
                entry = self._sessions.get(sid)
                if entry and entry.tenant_id in self._tenant_sessions:
                    self._tenant_sessions[entry.tenant_id].remove(sid)
            return len(expired)


# Global cache instance
_global_cache: SessionCache | None = None
_cache_lock = threading.Lock()


def get_global_cache(max_sessions: int = 50, max_per_tenant: int = 10) -> SessionCache:
    """Get or create the global session cache."""
    global _global_cache
    if _global_cache is None:
        with _cache_lock:
            if _global_cache is None:
                _global_cache = SessionCache(
                    max_sessions=max_sessions, max_per_tenant=max_per_tenant
                )
    return _global_cache


def reset_cache_for_tests() -> None:
    """Reset global cache (tests only)."""
    global _global_cache
    with _cache_lock:
        _global_cache = None
