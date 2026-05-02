"""Quota tier definitions and resolution."""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel


class TierLimits(BaseModel):
    """Per-tier quota limits."""

    memories_limit: int
    embed_tokens_daily: int
    writes_per_minute: int
    reads_per_minute: int


TIERS: Final[dict[str, TierLimits]] = {
    "standard": TierLimits(
        memories_limit=5_000,
        embed_tokens_daily=25_000,
        writes_per_minute=60,
        reads_per_minute=300,
    ),
    "premium": TierLimits(
        memories_limit=25_000,
        embed_tokens_daily=100_000,
        writes_per_minute=120,
        reads_per_minute=600,
    ),
    "gold": TierLimits(
        memories_limit=100_000,
        embed_tokens_daily=500_000,
        writes_per_minute=240,
        reads_per_minute=1_200,
    ),
    "platinum": TierLimits(
        memories_limit=500_000,
        embed_tokens_daily=2_000_000,
        writes_per_minute=600,
        reads_per_minute=3_000,
    ),
}


def resolve_tier(tier: str, override: dict[str, int] | None = None) -> TierLimits:
    """Resolve tier limits with optional per-tenant overrides.

    Args:
        tier: The tier name (standard, premium, gold, platinum).
        override: Optional dict of limit overrides. Unknown tier defaults to premium.

    Returns:
        TierLimits with applied overrides.
    """
    base = TIERS.get(tier)
    if base is None:
        # Default to premium for unknown tier (matches DB default per §8.3)
        base = TIERS["premium"]
    if not override:
        return base
    merged = base.model_dump()
    for k in ("memories_limit", "embed_tokens_daily", "writes_per_minute", "reads_per_minute"):
        if k in override:
            merged[k] = int(override[k])
    return TierLimits(**merged)
