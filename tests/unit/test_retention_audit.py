"""Tests for mem_mcp.jobs.retention_audit (T-7.15)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from mem_mcp.jobs.retention_audit import RetentionAuditStats, run


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


class TestRunAnonymizesTenants:
    """Test anonymization of audit rows for deleted tenants."""

    @pytest.mark.asyncio
    async def test_anonymizes_tenants_deleted_past_90d(self) -> None:
        """Anonymize audit_log for tenants deleted > 90d."""
        sample_tenant_id = uuid4()
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [{"tenant_id": sample_tenant_id}]
        mock_conn.execute.side_effect = ["UPDATE 10", "DELETE 5"]

        with patch(
            "mem_mcp.jobs.retention_audit.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            stats = await run(AsyncMock(), dry_run=False)

        assert stats.anonymized_count == 10
        assert stats.hard_deleted_count == 5
        # Verify fetch was called for tenant discovery
        assert mock_conn.fetch.call_count == 1
        fetch_call = mock_conn.fetch.call_args_list[0]
        assert "tenant.deleted" in str(fetch_call)
        assert "90 days" in str(fetch_call)

    @pytest.mark.asyncio
    async def test_skips_recently_deleted_tenants(self) -> None:
        """Don't anonymize if deletion < 90 days ago."""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_conn.execute.return_value = "DELETE 0"

        with patch(
            "mem_mcp.jobs.retention_audit.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            stats = await run(AsyncMock(), dry_run=False)

        assert stats.anonymized_count == 0
        # DELETE should still run on hard-delete
        assert mock_conn.execute.call_count == 1


class TestRunHardDelete:
    """Test hard-delete of audit rows past 730 days."""

    @pytest.mark.asyncio
    async def test_hard_deletes_past_730d(self) -> None:
        """DELETE from audit_log WHERE created_at < now() - 730 days."""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_conn.execute.return_value = "DELETE 12"

        with patch(
            "mem_mcp.jobs.retention_audit.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            stats = await run(AsyncMock(), dry_run=False)

        assert stats.hard_deleted_count == 12
        # Verify hard-delete query has 730 days check
        delete_call = mock_conn.execute.call_args_list[0]
        assert "730 days" in str(delete_call)


class TestRunDryRun:
    """Test dry_run mode."""

    @pytest.mark.asyncio
    async def test_dry_run_no_writes(self) -> None:
        """dry_run=True should COUNT only, no UPDATE/DELETE."""
        sample_tenant_id = uuid4()
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [{"tenant_id": sample_tenant_id}]
        mock_conn.fetchrow.side_effect = [
            {"cnt": 3},  # anonymize count
            {"cnt": 1},  # hard-delete count
        ]

        with patch(
            "mem_mcp.jobs.retention_audit.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            stats = await run(AsyncMock(), dry_run=True)

        assert stats.anonymized_count == 3
        assert stats.hard_deleted_count == 1
        # Verify no execute calls (no writes)
        assert mock_conn.execute.call_count == 0
        # Verify fetchrow was called for COUNTs
        assert mock_conn.fetchrow.call_count == 2


class TestRunReturnsStats:
    """Test that run() returns stats dataclass."""

    @pytest.mark.asyncio
    async def test_run_returns_retention_audit_stats(self) -> None:
        """run() should return RetentionAuditStats."""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_conn.execute.return_value = "DELETE 0"

        with patch(
            "mem_mcp.jobs.retention_audit.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            result = await run(AsyncMock(), dry_run=False)

        assert isinstance(result, RetentionAuditStats)
        assert hasattr(result, "anonymized_count")
        assert hasattr(result, "hard_deleted_count")


class TestMainCLI:
    """Test main() CLI entry point."""

    @pytest.mark.asyncio
    async def test_main_returns_total_affected_count(self) -> None:
        """main() should return sum of anonymized + hard_deleted."""
        sample_tenant_id = uuid4()
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [{"tenant_id": sample_tenant_id}]
        mock_conn.execute.side_effect = ["UPDATE 2", "DELETE 3"]
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()

        async def mock_create_pool(*args: object, **kwargs: object) -> AsyncMock:
            return mock_pool

        with patch("mem_mcp.jobs.retention_audit.system_tx", create_mock_system_tx(mock_conn)):
            with patch("asyncpg.create_pool", side_effect=mock_create_pool):
                with patch("mem_mcp.jobs.retention_audit.get_settings") as mock_settings:
                    mock_settings.return_value.log_level = "INFO"
                    mock_settings.return_value.db_maint_dsn = "postgresql://..."

                    from mem_mcp.jobs.retention_audit import main

                    result = await main(dry_run=False)

                    assert result == 5  # 2 + 3
