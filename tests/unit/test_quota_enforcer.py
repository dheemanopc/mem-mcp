"""Tests for QuotaEnforcer (T-7.9)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.quotas.enforcer import QuotaEnforcer
from mem_mcp.quotas.tiers import TIERS
from mem_mcp.ratelimit.token_bucket import _reset_buckets_for_tests


class _StubEmbeddings:
    """Stub embeddings client."""

    async def embed(self, text: str) -> None:  # type: ignore[no-untyped-def]
        pass


def _patch_system_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock | None = None) -> None:
    """Patch system_tx to yield our fake conn."""

    @asynccontextmanager
    async def fake_system_tx(pool: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.quotas.enforcer.system_tx", fake_system_tx)


def _patch_tenant_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock | None = None) -> None:
    """Patch tenant_tx to yield our fake conn."""

    @asynccontextmanager
    async def fake_tenant_tx(pool: Any, tenant_id: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.quotas.enforcer.tenant_tx", fake_tenant_tx)


class TestQuotaEnforcer:
    """Tests for QuotaEnforcer quota enforcement."""

    @pytest.fixture(autouse=True)
    def _reset(self) -> None:
        """Reset token buckets before each test."""
        _reset_buckets_for_tests()

    @pytest.mark.asyncio
    async def test_check_write_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """check_write succeeds with premium tier, 0 memories, 0 tokens used."""
        tenant_id = uuid4()
        pool = MagicMock()

        # Mock system_tx for tier resolution
        system_conn = AsyncMock()
        system_conn.fetchrow.return_value = {
            "tier": "premium",
            "limits_override": None,
        }
        _patch_system_tx(monkeypatch, system_conn)

        # Mock tenant_tx for memory count
        tenant_conn = AsyncMock()
        tenant_conn.fetchval.side_effect = [
            0,  # memory count
            0,  # today's embed_tokens
        ]
        _patch_tenant_tx(monkeypatch, tenant_conn)

        enforcer = QuotaEnforcer(pool)
        await enforcer.check_write(tenant_id, content_len_estimate=100)
        # Should not raise

    @pytest.mark.asyncio
    async def test_check_write_per_minute_exceeded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """check_write raises quota_exceeded for writes_per_minute limit."""
        tenant_id = uuid4()
        pool = MagicMock()

        # Mock system_tx for tier resolution (standard tier has 60 writes/min)
        system_conn = AsyncMock()
        system_conn.fetchrow.return_value = {
            "tier": "standard",
            "limits_override": None,
        }
        _patch_system_tx(monkeypatch, system_conn)

        # Mock tenant_tx
        tenant_conn = AsyncMock()
        tenant_conn.fetchval.return_value = 0  # memory count, token count
        _patch_tenant_tx(monkeypatch, tenant_conn)

        enforcer = QuotaEnforcer(pool)

        # Exhaust the bucket: standard is 60 writes/min, capacity = 60
        for _ in range(60):
            await enforcer.check_write(tenant_id, content_len_estimate=100)

        # The 61st should fail
        with pytest.raises(JsonRpcError) as exc_info:
            await enforcer.check_write(tenant_id, content_len_estimate=100)

        err = exc_info.value
        assert err.code == -32000
        assert err.message == "quota exceeded"
        assert err.data is not None
        assert err.data["code"] == "quota_exceeded"
        assert err.data["quota"] == "writes_per_minute"
        assert err.data["tier"] == "standard"
        assert "reset_at" in err.data
        assert "upgrade_url" in err.data

    @pytest.mark.asyncio
    async def test_check_write_memories_count_exceeded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """check_write raises quota_exceeded when memory count at limit."""
        tenant_id = uuid4()
        pool = MagicMock()

        # Premium tier has 25_000 memory limit
        system_conn = AsyncMock()
        system_conn.fetchrow.return_value = {
            "tier": "premium",
            "limits_override": None,
        }
        _patch_system_tx(monkeypatch, system_conn)

        # Memory count is at limit
        tenant_conn = AsyncMock()
        tenant_conn.fetchval.return_value = 25_000
        _patch_tenant_tx(monkeypatch, tenant_conn)

        enforcer = QuotaEnforcer(pool)
        with pytest.raises(JsonRpcError) as exc_info:
            await enforcer.check_write(tenant_id, content_len_estimate=100)

        err = exc_info.value
        assert err.code == -32000
        assert err.data["code"] == "quota_exceeded"
        assert err.data["quota"] == "memories_count"
        assert err.data["tier"] == "premium"

    @pytest.mark.asyncio
    async def test_check_write_embed_tokens_daily_exceeded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """check_write raises when daily embed tokens + estimate exceeds limit."""
        tenant_id = uuid4()
        pool = MagicMock()

        system_conn = AsyncMock()
        system_conn.fetchrow.return_value = {
            "tier": "premium",
            "limits_override": None,
        }
        _patch_system_tx(monkeypatch, system_conn)

        # Premium is 100_000 tokens/day. Today we've used 99_500.
        # Estimate = 100 // 4 = 25. Total would be 99_525, still under.
        # But estimate = 2000 // 4 = 500 would push to 100_000, which is at limit.
        tenant_conn = AsyncMock()
        tenant_conn.fetchval.side_effect = [
            0,  # memory count
            99_501,  # today's embed_tokens
        ]
        _patch_tenant_tx(monkeypatch, tenant_conn)

        enforcer = QuotaEnforcer(pool)

        # Estimate = 2000 // 4 = 500; 99_501 + 500 = 100_001 > 100_000
        with pytest.raises(JsonRpcError) as exc_info:
            await enforcer.check_write(tenant_id, content_len_estimate=2000)

        err = exc_info.value
        assert err.data["code"] == "quota_exceeded"
        assert err.data["quota"] == "embed_tokens_daily"

    @pytest.mark.asyncio
    async def test_check_read_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """check_read succeeds with premium tier and no rate limit exceeded."""
        tenant_id = uuid4()
        pool = MagicMock()

        system_conn = AsyncMock()
        system_conn.fetchrow.return_value = {
            "tier": "premium",
            "limits_override": None,
        }
        _patch_system_tx(monkeypatch, system_conn)
        _patch_tenant_tx(monkeypatch, None)

        enforcer = QuotaEnforcer(pool)
        await enforcer.check_read(tenant_id)
        # Should not raise

    @pytest.mark.asyncio
    async def test_check_read_per_minute_exceeded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """check_read raises quota_exceeded when reads_per_minute limit hit."""
        tenant_id = uuid4()
        pool = MagicMock()

        # Standard tier: 300 reads/min
        system_conn = AsyncMock()
        system_conn.fetchrow.return_value = {
            "tier": "standard",
            "limits_override": None,
        }
        _patch_system_tx(monkeypatch, system_conn)
        _patch_tenant_tx(monkeypatch, None)

        enforcer = QuotaEnforcer(pool)

        # Exhaust the bucket
        for _ in range(300):
            await enforcer.check_read(tenant_id)

        # 301st should fail
        with pytest.raises(JsonRpcError) as exc_info:
            await enforcer.check_read(tenant_id)

        err = exc_info.value
        assert err.data["code"] == "quota_exceeded"
        assert err.data["quota"] == "reads_per_minute"

    @pytest.mark.asyncio
    async def test_increment_write_upserts_daily_usage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """increment_write UPSERTs into tenant_daily_usage."""
        tenant_id = uuid4()
        pool = MagicMock()

        tenant_conn = AsyncMock()
        _patch_tenant_tx(monkeypatch, tenant_conn)

        enforcer = QuotaEnforcer(pool)
        await enforcer.increment_write(tenant_id, embed_tokens=1000)

        # Check that execute was called with the UPSERT
        tenant_conn.execute.assert_called_once()
        call_args = tenant_conn.execute.call_args
        sql = call_args[0][0]
        assert "INSERT INTO tenant_daily_usage" in sql
        assert "ON CONFLICT" in sql
        assert "embed_tokens" in sql
        assert "writes_count" in sql

    @pytest.mark.asyncio
    async def test_increment_read_upserts_daily_usage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """increment_read UPSERTs into tenant_daily_usage."""
        tenant_id = uuid4()
        pool = MagicMock()

        tenant_conn = AsyncMock()
        _patch_tenant_tx(monkeypatch, tenant_conn)

        enforcer = QuotaEnforcer(pool)
        await enforcer.increment_read(tenant_id, embed_tokens=500)

        # Check that execute was called with the UPSERT
        tenant_conn.execute.assert_called_once()
        call_args = tenant_conn.execute.call_args
        sql = call_args[0][0]
        assert "INSERT INTO tenant_daily_usage" in sql
        assert "ON CONFLICT" in sql
        assert "reads_count" in sql

    @pytest.mark.asyncio
    async def test_resolve_tier_with_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_resolve_tier applies limits_override correctly."""
        tenant_id = uuid4()
        pool = MagicMock()

        system_conn = AsyncMock()
        system_conn.fetchrow.return_value = {
            "tier": "premium",
            "limits_override": {"memories_limit": 99},
        }
        _patch_system_tx(monkeypatch, system_conn)

        enforcer = QuotaEnforcer(pool)
        tier_name, limits = await enforcer._resolve_tier(tenant_id)

        assert tier_name == "premium"
        assert limits.memories_limit == 99
        # Other limits should be from premium tier
        assert limits.embed_tokens_daily == 100_000

    @pytest.mark.asyncio
    async def test_resolve_tier_unknown_defaults_to_premium(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_resolve_tier falls back to premium for unknown tier."""
        tenant_id = uuid4()
        pool = MagicMock()

        system_conn = AsyncMock()
        system_conn.fetchrow.return_value = {
            "tier": "unknown_tier",
            "limits_override": None,
        }
        _patch_system_tx(monkeypatch, system_conn)

        enforcer = QuotaEnforcer(pool)
        tier_name, limits = await enforcer._resolve_tier(tenant_id)

        assert limits == TIERS["premium"]
