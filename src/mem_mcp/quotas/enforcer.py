"""Real quota enforcer implementing the Quotas Protocol (T-7.9).

Enforces per-minute rate limits (token bucket), memories count, and daily
embed token budget per spec §11.2 + LLD §4.7.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from mem_mcp.db.tenant_tx import system_tx, tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.quotas.tiers import TIERS, TierLimits, resolve_tier
from mem_mcp.ratelimit.token_bucket import get_bucket

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

QuotaName = Literal[
    "memories_count",
    "embed_tokens_daily",
    "writes_per_minute",
    "reads_per_minute",
]

# Asia/Kolkata timezone for daily quota reset
_IST = ZoneInfo("Asia/Kolkata")

# Static upgrade URL per NFR-9.4.4 v1
_UPGRADE_URL = "https://memapp.dheemantech.in/billing"


def _today_ist() -> date:
    """Return today's date in IST as a date object."""
    return datetime.now(tz=_IST).date()


def _next_midnight_ist() -> datetime:
    """Return next midnight in IST as an aware datetime."""
    now_ist = datetime.now(tz=_IST)
    tomorrow = now_ist.date() + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=_IST)


class QuotaEnforcer:
    """Real Quotas implementation per Protocol in mcp.tools._deps.

    Enforces:
      - Per-minute rate limits via in-process token buckets
      - Memory count limits
      - Daily embed token budgets
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Initialize the enforcer with a database pool.

        Args:
            pool: asyncpg.Pool for database access.
        """
        self._pool = pool

    async def _resolve_tier(self, tenant_id: UUID) -> tuple[str, TierLimits]:
        """Resolve tenant tier and limits, applying overrides.

        Args:
            tenant_id: The tenant UUID.

        Returns:
            (tier_name, TierLimits) with overrides applied.
        """
        async with system_tx(self._pool) as conn:
            row = await conn.fetchrow(
                "SELECT tier, limits_override FROM tenants WHERE id = $1",
                tenant_id,
            )

        if row is None:
            # Unknown tenant; default to premium
            return "premium", TIERS["premium"]

        tier_name = row["tier"]
        limits_override = row["limits_override"]
        limits = resolve_tier(tier_name, override=limits_override)
        return tier_name, limits

    async def _today_usage(self, tenant_id: UUID) -> tuple[int, int]:
        """Fetch today's usage (embed_tokens, writes_count) for the tenant.

        Uses tenant_tx for RLS isolation. Returns (0, 0) if no row exists.

        Args:
            tenant_id: The tenant UUID.

        Returns:
            (embed_tokens, writes_count) for today (IST).
        """
        async with tenant_tx(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT embed_tokens, writes_count FROM tenant_daily_usage WHERE usage_date = $1 AND tenant_id = $2",
                _today_ist(),
                tenant_id,
            )

        if row is None:
            return 0, 0
        return int(row["embed_tokens"] or 0), int(row["writes_count"] or 0)

    async def check_write(self, tenant_id: UUID, content_len_estimate: int) -> None:
        """Check write quota before accepting a memory.write call.

        Enforces:
          1. Per-minute write rate limit
          2. Memories count limit
          3. Daily embed token budget

        Raises JsonRpcError(-32000) if any quota exceeded.

        Args:
            tenant_id: The tenant UUID.
            content_len_estimate: Estimated content length (used to compute embed token estimate).

        Raises:
            JsonRpcError: -32000 with quota_exceeded data if any limit exceeded.
        """
        tier_name, limits = await self._resolve_tier(tenant_id)

        # 1. Per-minute write rate
        bucket = get_bucket(
            f"tenant:{tenant_id}:write",
            capacity=limits.writes_per_minute,
            refill_per_sec=limits.writes_per_minute / 60.0,
        )
        if not bucket.try_take(1):
            reset_at = _next_midnight_ist()
            raise JsonRpcError(
                -32000,
                "quota exceeded",
                data={
                    "code": "quota_exceeded",
                    "quota": "writes_per_minute",
                    "tier": tier_name,
                    "reset_at": reset_at.isoformat(),
                    "upgrade_url": _UPGRADE_URL,
                },
            )

        # 2. Memory count limit
        async with tenant_tx(self._pool, tenant_id) as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM memories WHERE tenant_id = $1 AND deleted_at IS NULL",
                tenant_id,
            )
        count = int(count or 0)
        if count >= limits.memories_limit:
            reset_at = _next_midnight_ist()
            raise JsonRpcError(
                -32000,
                "quota exceeded",
                data={
                    "code": "quota_exceeded",
                    "quota": "memories_count",
                    "tier": tier_name,
                    "reset_at": reset_at.isoformat(),
                    "upgrade_url": _UPGRADE_URL,
                },
            )

        # 3. Daily embed token budget
        embed_tokens_used, _ = await self._today_usage(tenant_id)
        embed_tokens_estimated = content_len_estimate // 4
        if embed_tokens_used + embed_tokens_estimated > limits.embed_tokens_daily:
            reset_at = _next_midnight_ist()
            raise JsonRpcError(
                -32000,
                "quota exceeded",
                data={
                    "code": "quota_exceeded",
                    "quota": "embed_tokens_daily",
                    "tier": tier_name,
                    "reset_at": reset_at.isoformat(),
                    "upgrade_url": _UPGRADE_URL,
                },
            )

    async def check_read(self, tenant_id: UUID) -> None:
        """Check read quota before accepting a memory.read call.

        Enforces per-minute read rate limit.

        Raises JsonRpcError(-32000) if quota exceeded.

        Args:
            tenant_id: The tenant UUID.

        Raises:
            JsonRpcError: -32000 with quota_exceeded data if limit exceeded.
        """
        tier_name, limits = await self._resolve_tier(tenant_id)

        # Per-minute read rate
        bucket = get_bucket(
            f"tenant:{tenant_id}:read",
            capacity=limits.reads_per_minute,
            refill_per_sec=limits.reads_per_minute / 60.0,
        )
        if not bucket.try_take(1):
            reset_at = _next_midnight_ist()
            raise JsonRpcError(
                -32000,
                "quota exceeded",
                data={
                    "code": "quota_exceeded",
                    "quota": "reads_per_minute",
                    "tier": tier_name,
                    "reset_at": reset_at.isoformat(),
                    "upgrade_url": _UPGRADE_URL,
                },
            )

    async def increment_write(self, tenant_id: UUID, embed_tokens: int) -> None:
        """Record a write and its embed token cost to tenant_daily_usage.

        Upserts into tenant_daily_usage keyed on (tenant_id, usage_date in IST).
        Increments embed_tokens and writes_count.

        Args:
            tenant_id: The tenant UUID.
            embed_tokens: The number of tokens spent on this write.
        """
        async with tenant_tx(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO tenant_daily_usage
                  (tenant_id, usage_date, embed_tokens, writes_count, reads_count, deletes_count)
                VALUES ($1, $2, $3, 1, 0, 0)
                ON CONFLICT (tenant_id, usage_date) DO UPDATE
                SET embed_tokens = tenant_daily_usage.embed_tokens + EXCLUDED.embed_tokens,
                    writes_count = tenant_daily_usage.writes_count + EXCLUDED.writes_count
                """,
                tenant_id,
                _today_ist(),
                embed_tokens,
            )

    async def increment_read(self, tenant_id: UUID, embed_tokens: int) -> None:
        """Record a read and its embed token cost to tenant_daily_usage.

        Upserts into tenant_daily_usage keyed on (tenant_id, usage_date in IST).
        Increments embed_tokens and reads_count.

        Args:
            tenant_id: The tenant UUID.
            embed_tokens: The number of tokens spent on this read.
        """
        async with tenant_tx(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO tenant_daily_usage
                  (tenant_id, usage_date, embed_tokens, writes_count, reads_count, deletes_count)
                VALUES ($1, $2, $3, 0, 1, 0)
                ON CONFLICT (tenant_id, usage_date) DO UPDATE
                SET embed_tokens = tenant_daily_usage.embed_tokens + EXCLUDED.embed_tokens,
                    reads_count = tenant_daily_usage.reads_count + EXCLUDED.reads_count
                """,
                tenant_id,
                _today_ist(),
                embed_tokens,
            )
