"""Tests for memory.stats tool (T-7.7)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mem_mcp.audit.logger import NoopAuditLogger
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._deps import NoopQuotas, ToolDeps
from mem_mcp.mcp.tools.stats import (
    MemoryStatsInput,
    MemoryStatsOutput,
    MemoryStatsTool,
)


def _ctx(db_pool: Any | None = None) -> ToolContext:
    """Build a test ToolContext with mocked deps."""
    deps = ToolDeps(
        embeddings=MagicMock(),
        audit=NoopAuditLogger(),
        quotas=NoopQuotas(),
    )
    return ToolContext(
        request_id=str(uuid4()),
        tenant_id=uuid4(),
        identity_id=uuid4(),
        client_id="client-1",
        scopes=frozenset(("memory.read",)),
        db_pool=db_pool or MagicMock(),
        deps=deps,
    )


def _patch_tenant_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    """Patch tenant_tx to yield our fake conn."""

    @asynccontextmanager
    async def fake_tx(pool: Any, tenant_id: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.mcp.tools.stats.tenant_tx", fake_tx)


class TestMemoryStatsInput:
    def test_no_input_required(self) -> None:
        """Stats takes no input parameters."""
        inp = MemoryStatsInput()
        # Should be empty/simple
        assert isinstance(inp, MemoryStatsInput)


class TestMemoryStatsTool:
    @pytest.mark.asyncio
    async def test_stats_output_structure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that stats returns proper structure."""
        tool = MemoryStatsTool()
        ctx = _ctx()
        inp = MemoryStatsInput()

        # Mock conn with multiple fetch/fetchrow calls
        mock_conn = AsyncMock()

        # Results for each query:
        # 1. by_type counts
        type_counts = [
            {"type": "note", "count": 800},
            {"type": "decision", "count": 200},
        ]
        # 2. top 10 tags
        top_tags = [
            {"tag": "project:main", "count": 150},
            {"tag": "priority:high", "count": 120},
        ]
        # 3. oldest/newest
        bounds = {
            "min_created": datetime(2025, 1, 1, tzinfo=UTC),
            "max_created": datetime.now(tz=UTC),
        }
        # 4. today's usage
        today_usage = {
            "writes_count": 10,
            "reads_count": 50,
            "embed_tokens": 5000,
        }
        # 5. tenant row
        tenant_row = {
            "tier": "premium",
            "limits_override": None,
        }

        call_count = 0

        async def mock_fetch(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return type_counts
            elif call_count == 2:
                return top_tags
            return []

        async def mock_fetchrow(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                return bounds
            elif call_count == 4:
                return today_usage
            elif call_count == 5:
                return tenant_row
            return None

        mock_conn.fetch = mock_fetch
        mock_conn.fetchrow = mock_fetchrow

        _patch_tenant_tx(monkeypatch, mock_conn)

        result = await tool(ctx, inp)
        assert isinstance(result, MemoryStatsOutput)
        assert result.total_memories == 1000  # 800 + 200
        assert result.by_type["note"] == 800
        assert result.by_type["decision"] == 200
        assert len(result.top_tags) == 2
        assert result.top_tags[0].tag == "project:main"
        assert result.oldest == bounds["min_created"]
        assert result.newest == bounds["max_created"]
        assert result.today.writes == 10
        assert result.today.reads == 50
        assert result.today.embed_tokens == 5000
        assert result.quota.tier == "premium"
        assert result.request_id == ctx.request_id

    @pytest.mark.asyncio
    async def test_top_tags_limited_to_10(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that top_tags is limited to 10 items."""
        tool = MemoryStatsTool()
        ctx = _ctx()
        inp = MemoryStatsInput()

        mock_conn = AsyncMock()

        # type counts (minimal)
        type_counts: list[dict[str, Any]] = []

        # top 10 tags (DB query LIMIT 10 already enforces this)
        top_tags = [{"tag": f"tag{i}", "count": 100 - i} for i in range(10)]

        bounds = {
            "min_created": datetime(2025, 1, 1, tzinfo=UTC),
            "max_created": datetime.now(tz=UTC),
        }

        today_usage = {
            "writes_count": 0,
            "reads_count": 0,
            "embed_tokens": 0,
        }

        tenant_row = {
            "tier": "standard",
            "limits_override": None,
        }

        call_count = 0

        async def mock_fetch(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return type_counts
            elif call_count == 2:
                return top_tags
            return []

        async def mock_fetchrow(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                return bounds
            elif call_count == 4:
                return today_usage
            elif call_count == 5:
                return tenant_row
            return None

        mock_conn.fetch = mock_fetch
        mock_conn.fetchrow = mock_fetchrow

        _patch_tenant_tx(monkeypatch, mock_conn)

        result = await tool(ctx, inp)
        assert isinstance(result, MemoryStatsOutput)
        assert len(result.top_tags) == 10

    @pytest.mark.asyncio
    async def test_unknown_tier_defaults_to_premium(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that unknown tier defaults to premium."""
        tool = MemoryStatsTool()
        ctx = _ctx()
        inp = MemoryStatsInput()

        mock_conn = AsyncMock()

        type_counts: list[dict[str, Any]] = []
        top_tags: list[dict[str, Any]] = []

        bounds = {
            "min_created": None,
            "max_created": None,
        }

        today_usage = {
            "writes_count": 0,
            "reads_count": 0,
            "embed_tokens": 0,
        }

        tenant_row = {
            "tier": "unknown_tier_xyz",
            "limits_override": None,
        }

        call_count = 0

        async def mock_fetch(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return type_counts
            elif call_count == 2:
                return top_tags
            return []

        async def mock_fetchrow(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                return bounds
            elif call_count == 4:
                return today_usage
            elif call_count == 5:
                return tenant_row
            return None

        mock_conn.fetch = mock_fetch
        mock_conn.fetchrow = mock_fetchrow

        _patch_tenant_tx(monkeypatch, mock_conn)

        result = await tool(ctx, inp)
        # Should fall back to premium
        assert isinstance(result, MemoryStatsOutput)
        assert result.quota.tier == "unknown_tier_xyz"
        assert result.quota.memories_limit == 25_000  # premium limit

    @pytest.mark.asyncio
    async def test_missing_today_usage_defaults_to_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that missing today_usage row defaults to zeros."""
        tool = MemoryStatsTool()
        ctx = _ctx()
        inp = MemoryStatsInput()

        mock_conn = AsyncMock()

        type_counts: list[dict[str, Any]] = []
        top_tags: list[dict[str, Any]] = []

        bounds = {
            "min_created": None,
            "max_created": None,
        }

        # No today_usage row
        today_usage = None

        tenant_row = {
            "tier": "standard",
            "limits_override": None,
        }

        call_count = 0

        async def mock_fetch(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return type_counts
            elif call_count == 2:
                return top_tags
            return []

        async def mock_fetchrow(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                return bounds
            elif call_count == 4:
                return today_usage
            elif call_count == 5:
                return tenant_row
            return None

        mock_conn.fetch = mock_fetch
        mock_conn.fetchrow = mock_fetchrow

        _patch_tenant_tx(monkeypatch, mock_conn)

        result = await tool(ctx, inp)
        assert isinstance(result, MemoryStatsOutput)
        assert result.today.writes == 0
        assert result.today.reads == 0
        assert result.today.embed_tokens == 0
