"""Tests for memory access-tracking (throttled bump on get/search)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mem_mcp.memory.access_tracking import (
    ACCESS_BUMP_THROTTLE_SECONDS,
    bump_access,
)


@pytest.fixture
def mock_pool() -> MagicMock:
    return MagicMock()


def _patch_tenant_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    @asynccontextmanager
    async def _fake_tenant_tx(pool: object, tenant_id: object) -> AsyncIterator[AsyncMock]:
        yield conn

    monkeypatch.setattr("mem_mcp.memory.access_tracking.tenant_tx", _fake_tenant_tx)


class TestBumpAccess:
    @pytest.mark.asyncio
    async def test_no_ids_is_noop(
        self, mock_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty list short-circuits — no asyncio task spawned, no DB call."""
        mock_conn = AsyncMock()
        _patch_tenant_tx(monkeypatch, mock_conn)
        await bump_access(mock_pool, uuid4(), [])
        # Yield so any (incorrectly) scheduled task would run
        await asyncio.sleep(0)
        mock_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_filters_none_ids(
        self, mock_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mixed None entries are filtered out before scheduling."""
        mock_conn = AsyncMock()
        _patch_tenant_tx(monkeypatch, mock_conn)
        await bump_access(mock_pool, uuid4(), [None, None])  # type: ignore[list-item]
        await asyncio.sleep(0)
        mock_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_id_schedules_update(
        self, mock_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-empty ID list triggers a batched UPDATE with the right shape."""
        mock_conn = AsyncMock()
        _patch_tenant_tx(monkeypatch, mock_conn)
        tid = uuid4()
        mid = uuid4()
        await bump_access(mock_pool, tid, [mid])
        # bump_access spawns asyncio.create_task — give it a tick to run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        mock_conn.execute.assert_called_once()
        sql, ids_arg = mock_conn.execute.call_args.args
        # SQL must batch via id = ANY($1), set last_accessed_at + access_count,
        # AND throttle on the 5-min window.
        assert "UPDATE memories" in sql
        assert "= ANY($1" in sql
        assert "last_accessed_at = now()" in sql
        assert "access_count = access_count + 1" in sql
        assert "last_accessed_at IS NULL" in sql
        assert f"{ACCESS_BUMP_THROTTLE_SECONDS} seconds" in sql
        assert ids_arg == [mid]

    @pytest.mark.asyncio
    async def test_batch_update_from_search(
        self, mock_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Many IDs (search-result case) issue exactly ONE UPDATE, not N."""
        mock_conn = AsyncMock()
        _patch_tenant_tx(monkeypatch, mock_conn)
        ids = [uuid4() for _ in range(20)]
        await bump_access(mock_pool, uuid4(), ids)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # ONE call, with ALL ids in the array arg
        assert mock_conn.execute.call_count == 1
        _sql, ids_arg = mock_conn.execute.call_args.args
        assert ids_arg == ids

    @pytest.mark.asyncio
    async def test_db_failure_is_swallowed(
        self, mock_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A DB error during bump must not raise to the caller (fire-and-forget)."""
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = RuntimeError("boom")
        _patch_tenant_tx(monkeypatch, mock_conn)
        # Should return cleanly even though the bump will fail in the background.
        await bump_access(mock_pool, uuid4(), [uuid4()])
        # Yield so the task runs and the exception fires inside it
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # If the exception had propagated, this test would already have failed
        # at await bump_access(...). Reaching here proves swallow.
