"""Per-tenant instruments cache with daily refresh (IST-based).

Caches symbol→instrument_token mappings. Refreshes when the IST trading date changes.
Prevents thundering herd via per-key asyncio locks.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from mem_mcp.skills.base import SkillError
from mem_mcp.skills.kite.client import KiteClient

logger = logging.getLogger(__name__)

# IST timezone
IST = ZoneInfo("Asia/Kolkata")


class InstrumentsCache:
    """Per-tenant, per-exchange symbol→token lookup with daily refresh."""

    def __init__(self) -> None:
        # (tenant_id, exchange) -> (symbol_to_token dict, fetched_at datetime)
        self._cache: dict[tuple[str, str], tuple[dict[str, int], datetime]] = {}
        # Per (tenant_id, exchange) locks to prevent thundering herd
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _get_lock(self, tenant_id: str, exchange: str) -> asyncio.Lock:
        """Get or create a lock for this (tenant, exchange)."""
        key = (tenant_id, exchange)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def _is_cache_stale(self, fetched_at: datetime) -> bool:
        """Check if cache is older than today in IST."""
        fetched_date = fetched_at.astimezone(IST).date()
        today_ist = datetime.now(IST).date()
        return fetched_date < today_ist

    async def get_token(
        self,
        *,
        tenant_id: str,
        symbol: str,
        client: KiteClient,
        api_key: str,
        access_token: str,
        exchange: str = "NSE",
    ) -> int:
        """Return instrument_token for the given symbol.

        Refetches the instruments master if cache miss or stale (older than
        today in IST). Raises SkillError if the symbol is not found.
        """
        key = (tenant_id, exchange)
        lock = self._get_lock(tenant_id, exchange)

        # Check cache first (non-locking read)
        if key in self._cache:
            symbol_to_token, fetched_at = self._cache[key]
            if not self._is_cache_stale(fetched_at) and symbol in symbol_to_token:
                return symbol_to_token[symbol]

        # Cache miss or stale; acquire lock and re-check (double-checked locking)
        async with lock:
            # Re-check after acquiring lock
            if key in self._cache:
                symbol_to_token, fetched_at = self._cache[key]
                if not self._is_cache_stale(fetched_at) and symbol in symbol_to_token:
                    return symbol_to_token[symbol]

            # Fetch fresh instruments master
            logger.info(
                "fetching_instruments_master",
                extra={
                    "tenant_id": tenant_id,
                    "exchange": exchange,
                },
            )
            instruments = await client.get_instruments(
                api_key=api_key,
                access_token=access_token,
                exchange=exchange,
            )

            # Build symbol -> token map
            symbol_to_token = {}
            for inst in instruments:
                ts = inst.get("tradingsymbol")
                tok = inst.get("instrument_token")
                if ts and tok is not None:
                    symbol_to_token[ts] = int(tok) if isinstance(tok, str) else tok

            # Cache it with current timestamp
            now = datetime.now(IST)
            self._cache[key] = (symbol_to_token, now)

            logger.info(
                "instruments_master_cached",
                extra={
                    "tenant_id": tenant_id,
                    "exchange": exchange,
                    "symbols_count": len(symbol_to_token),
                },
            )

            # Look up the symbol
            if symbol not in symbol_to_token:
                raise SkillError(f"symbol '{symbol}' not found in {exchange} instruments master")

            return symbol_to_token[symbol]


# Global cache instance
_instruments_cache: InstrumentsCache | None = None
_cache_lock = asyncio.Lock()


def get_instruments_cache() -> InstrumentsCache:
    """Get or create the global instruments cache."""
    global _instruments_cache
    if _instruments_cache is None:
        _instruments_cache = InstrumentsCache()
    return _instruments_cache
