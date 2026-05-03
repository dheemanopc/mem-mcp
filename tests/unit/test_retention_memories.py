"""Tests for mem_mcp.jobs.retention_memories (T-7.14)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest

from mem_mcp.jobs.retention_memories import RetentionMemoriesStats, run


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


class TestRunSoftDelete:
    """Test soft-delete (mark deleted_at) for memories past retention_days."""

    @pytest.mark.asyncio
    async def test_soft_deletes_past_retention_days(self) -> None:
        """Soft-delete memories with created_at < now() - retention_days."""
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = ["UPDATE 10", "DELETE 5"]

        with patch(
            "mem_mcp.jobs.retention_memories.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            stats = await run(AsyncMock(), dry_run=False)

        assert stats.soft_deleted_count == 10
        assert stats.hard_deleted_count == 5
        assert mock_conn.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_soft_delete_with_per_tenant_retention(self) -> None:
        """Soft-delete uses per-tenant retention_days from tenants table."""
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = ["UPDATE 3", "DELETE 0"]

        with patch(
            "mem_mcp.jobs.retention_memories.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            _ = await run(AsyncMock(), dry_run=False)

        # Verify the UPDATE query has the per-tenant JOIN
        update_call = mock_conn.execute.call_args_list[0]
        assert "FROM tenants t" in str(update_call)
        assert "retention_days" in str(update_call)


class TestRunHardDelete:
    """Test hard-delete for memories past 30d grace period."""

    @pytest.mark.asyncio
    async def test_hard_deletes_past_30d_grace(self) -> None:
        """Hard-delete memories with deleted_at < now() - 30d."""
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = ["UPDATE 0", "DELETE 7"]

        with patch(
            "mem_mcp.jobs.retention_memories.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            stats = await run(AsyncMock(), dry_run=False)

        assert stats.hard_deleted_count == 7
        # Verify hard-delete uses 30 day interval
        delete_call = mock_conn.execute.call_args_list[1]
        assert "30 days" in str(delete_call)


class TestRunDryRun:
    """Test dry_run mode (COUNT only, no writes)."""

    @pytest.mark.asyncio
    async def test_dry_run_no_updates_only_counts(self) -> None:
        """dry_run=True should COUNT without UPDATE/DELETE."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            {"cnt": 5},  # soft-delete count
            {"cnt": 2},  # hard-delete count
        ]

        with patch(
            "mem_mcp.jobs.retention_memories.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            stats = await run(AsyncMock(), dry_run=True)

        assert stats.soft_deleted_count == 5
        assert stats.hard_deleted_count == 2
        # Verify no execute calls (no UPDATE/DELETE)
        assert mock_conn.execute.call_count == 0
        # Verify fetchrow was called for COUNTs
        assert mock_conn.fetchrow.call_count == 2

    @pytest.mark.asyncio
    async def test_dry_run_returns_stats(self) -> None:
        """dry_run=True should return correct stats."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [{"cnt": 0}, {"cnt": 1}]

        with patch(
            "mem_mcp.jobs.retention_memories.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            stats = await run(AsyncMock(), dry_run=True)

        assert isinstance(stats, RetentionMemoriesStats)
        assert stats.soft_deleted_count == 0
        assert stats.hard_deleted_count == 1


class TestRunReturnsStats:
    """Test that run() returns stats dataclass."""

    @pytest.mark.asyncio
    async def test_run_returns_retention_memories_stats(self) -> None:
        """run() should return RetentionMemoriesStats."""
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = ["UPDATE 0", "DELETE 0"]

        with patch(
            "mem_mcp.jobs.retention_memories.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            result = await run(AsyncMock(), dry_run=False)

        assert isinstance(result, RetentionMemoriesStats)
        assert hasattr(result, "soft_deleted_count")
        assert hasattr(result, "hard_deleted_count")


class TestMainCLI:
    """Test main() CLI entry point."""

    @pytest.mark.asyncio
    async def test_main_returns_total_affected_count(self) -> None:
        """main() should return sum of soft + hard deleted counts."""

        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = ["UPDATE 3", "DELETE 2"]
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()

        async def mock_create_pool(*args: object, **kwargs: object) -> AsyncMock:
            return mock_pool

        with patch("mem_mcp.jobs.retention_memories.system_tx", create_mock_system_tx(mock_conn)):
            with patch("asyncpg.create_pool", side_effect=mock_create_pool):
                with patch("mem_mcp.jobs.retention_memories.get_settings") as mock_settings:
                    mock_settings.return_value.log_level = "INFO"
                    mock_settings.return_value.db_maint_dsn = "postgresql://..."

                    from mem_mcp.jobs.retention_memories import main

                    result = await main(dry_run=False)

                    assert result == 5  # 3 + 2
