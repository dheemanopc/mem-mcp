"""Tests for mem_mcp.jobs.retention_tokens (T-7.14)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from mem_mcp.jobs.retention_tokens import RetentionTokensStats, run

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def create_mock_system_tx(
    mock_conn: AsyncMock,
) -> object:
    """Create a mock system_tx that yields the given connection."""

    @asynccontextmanager
    async def _mock_system_tx(pool: object) -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    return _mock_system_tx


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class TestRunPurgesLinkState:
    """Test purging expired link_state tokens."""

    @pytest.mark.asyncio
    async def test_purges_expired_link_state(self) -> None:
        """DELETE from link_state WHERE expires_at < now()."""
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = ["DELETE 4", "DELETE 2"]

        with patch(
            "mem_mcp.jobs.retention_tokens.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            stats = await run(AsyncMock(), dry_run=False)

        assert stats.link_state_purged == 4
        assert mock_conn.execute.call_count == 2
        # Verify link_state query checks expires_at < now()
        link_state_call = mock_conn.execute.call_args_list[0]
        assert "expires_at < now()" in str(link_state_call)


class TestRunPurgesWebSessions:
    """Test purging expired or revoked web sessions."""

    @pytest.mark.asyncio
    async def test_purges_expired_web_sessions(self) -> None:
        """DELETE from web_sessions WHERE expires_at < now()."""
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = ["DELETE 1", "DELETE 3"]

        with patch(
            "mem_mcp.jobs.retention_tokens.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            stats = await run(AsyncMock(), dry_run=False)

        assert stats.web_sessions_purged == 3

    @pytest.mark.asyncio
    async def test_purges_revoked_web_sessions_past_7d(self) -> None:
        """DELETE from web_sessions with revoked_at < now() - 7d."""
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = ["DELETE 0", "DELETE 5"]

        with patch(
            "mem_mcp.jobs.retention_tokens.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            _ = await run(AsyncMock(), dry_run=False)

        # Verify query includes revoked_at check
        web_sessions_call = mock_conn.execute.call_args_list[1]
        assert "revoked_at" in str(web_sessions_call)
        assert "7 days" in str(web_sessions_call)


class TestRunDryRun:
    """Test dry_run mode."""

    @pytest.mark.asyncio
    async def test_dry_run_no_deletes(self) -> None:
        """dry_run=True should COUNT only, no DELETE."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            {"cnt": 2},  # link_state count
            {"cnt": 1},  # web_sessions count
        ]

        with patch(
            "mem_mcp.jobs.retention_tokens.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            stats = await run(AsyncMock(), dry_run=True)

        assert stats.link_state_purged == 2
        assert stats.web_sessions_purged == 1
        # Verify no execute calls
        assert mock_conn.execute.call_count == 0
        # Verify fetchrow was called for COUNTs
        assert mock_conn.fetchrow.call_count == 2


class TestRunReturnsStats:
    """Test that run() returns stats dataclass."""

    @pytest.mark.asyncio
    async def test_run_returns_retention_tokens_stats(self) -> None:
        """run() should return RetentionTokensStats."""
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = ["DELETE 0", "DELETE 0"]

        with patch(
            "mem_mcp.jobs.retention_tokens.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            result = await run(AsyncMock(), dry_run=False)

        assert isinstance(result, RetentionTokensStats)
        assert hasattr(result, "link_state_purged")
        assert hasattr(result, "web_sessions_purged")


class TestMainCLI:
    """Test main() CLI entry point."""

    @pytest.mark.asyncio
    async def test_main_returns_total_purged_count(self) -> None:
        """main() should return sum of purged counts."""
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = ["DELETE 2", "DELETE 3"]
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()

        async def mock_create_pool(*args: object, **kwargs: object) -> AsyncMock:
            return mock_pool

        with patch("mem_mcp.jobs.retention_tokens.system_tx", create_mock_system_tx(mock_conn)):
            with patch("asyncpg.create_pool", side_effect=mock_create_pool):
                with patch("mem_mcp.jobs.retention_tokens.get_settings") as mock_settings:
                    mock_settings.return_value.log_level = "INFO"
                    mock_settings.return_value.db_maint_dsn = "postgresql://..."

                    from mem_mcp.jobs.retention_tokens import main

                    result = await main(dry_run=False)

                    assert result == 5  # 2 + 3
