"""Tests for mem_mcp.jobs.cleanup_kite_intents."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from mem_mcp.jobs.cleanup_kite_intents import GRACE_DAYS, run


def _mock_system_tx(mock_conn: AsyncMock) -> object:
    @asynccontextmanager
    async def _ctx(pool: object) -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    return _ctx


class TestCleanupKiteIntents:
    @pytest.mark.asyncio
    async def test_dry_run_counts_without_deleting(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 5

        with patch(
            "mem_mcp.jobs.cleanup_kite_intents.system_tx",
            _mock_system_tx(mock_conn),
        ):
            affected = await run(pool=AsyncMock(), dry_run=True)

        assert affected == 5
        # Only fetchval (COUNT) — no execute (DELETE)
        mock_conn.fetchval.assert_called_once()
        mock_conn.execute.assert_not_called()
        # Verify query targets both branches (resolved + unresolved)
        query = str(mock_conn.fetchval.call_args)
        assert "consumed_at IS NOT NULL" in query
        assert "consumed_at IS NULL" in query
        assert f"{GRACE_DAYS} days" in query

    @pytest.mark.asyncio
    async def test_apply_deletes_and_returns_count(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 12"

        with patch(
            "mem_mcp.jobs.cleanup_kite_intents.system_tx",
            _mock_system_tx(mock_conn),
        ):
            affected = await run(pool=AsyncMock(), dry_run=False)

        assert affected == 12
        mock_conn.execute.assert_called_once()
        mock_conn.fetchval.assert_not_called()
        # Verify the DELETE clause targets both stale-resolved and stale-unresolved
        query = str(mock_conn.execute.call_args)
        assert "DELETE FROM kite_intents" in query
        assert "consumed_at IS NOT NULL" in query
        assert "consumed_at IS NULL" in query
        assert "expires_at" in query

    @pytest.mark.asyncio
    async def test_apply_handles_malformed_delete_result(self) -> None:
        """If asyncpg returns something unexpected, fall back to 0 rather than crash."""
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "weird response"

        with patch(
            "mem_mcp.jobs.cleanup_kite_intents.system_tx",
            _mock_system_tx(mock_conn),
        ):
            affected = await run(pool=AsyncMock(), dry_run=False)

        # "weird response" -> last token "response" -> int() fails -> 0
        assert affected == 0

    @pytest.mark.asyncio
    async def test_apply_zero_rows(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 0"

        with patch(
            "mem_mcp.jobs.cleanup_kite_intents.system_tx",
            _mock_system_tx(mock_conn),
        ):
            affected = await run(pool=AsyncMock(), dry_run=False)

        assert affected == 0
