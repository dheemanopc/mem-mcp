"""Tests for mem_mcp.jobs.retention_deletion (T-7.12)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

from mem_mcp.jobs.retention_deletion import RetentionDeletionJob, RetentionDeletionStats

# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeCognitoAdminDeleter:
    async def admin_delete_user(self, cognito_username: str) -> None:
        pass


class FakeCognitoGlobalSignOutter:
    async def admin_user_global_sign_out(self, cognito_username: str) -> None:
        pass


class FakeCognitoClientDeleter:
    async def delete_user_pool_client(self, client_id: str) -> None:
        pass


class FakeAuditLogger:
    async def log_event(
        self,
        event_type: str,
        *,
        target_id: Any,
        details: dict[str, Any] | None = None,
        request_id: str,
    ) -> None:
        pass

    def log_error(
        self,
        message: str,
        *,
        exc_info: Exception | None = None,
        request_id: str,
    ) -> None:
        pass


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class TestRetentionDeletionJob:
    async def test_run_finalizes_pending_past_24h(self, monkeypatch: Any) -> None:
        """Mock SELECT returns 2 tenants → finalize_closure called twice → stats {scanned:2, finalized:2, failed:0}."""
        tenant1 = uuid4()
        tenant2 = uuid4()

        fake_conn = AsyncMock()
        fake_conn.fetch.return_value = [
            {"id": tenant1},
            {"id": tenant2},
        ]

        @asynccontextmanager
        async def fake_system_tx(pool: Any) -> AsyncGenerator[Any, None]:
            yield fake_conn

        # Mock finalize_closure to do nothing
        finalize_calls = []

        async def fake_finalize_closure(
            pool: Any,
            *,
            tenant_id: Any,
            deleter: Any,
            sign_outter: Any,
            client_deleter: Any,
            audit: Any,
            request_id: Any,
        ) -> None:
            finalize_calls.append(tenant_id)

        monkeypatch.setattr(
            "mem_mcp.jobs.retention_deletion.system_tx",
            fake_system_tx,
        )
        monkeypatch.setattr(
            "mem_mcp.jobs.retention_deletion.finalize_closure",
            fake_finalize_closure,
        )

        pool = AsyncMock()
        deleter = FakeCognitoAdminDeleter()
        sign_outter = FakeCognitoGlobalSignOutter()
        client_deleter = FakeCognitoClientDeleter()
        audit = FakeAuditLogger()

        job = RetentionDeletionJob(pool, deleter, sign_outter, client_deleter, audit)
        stats = await job.run(request_id="test-batch-1")

        assert isinstance(stats, RetentionDeletionStats)
        assert stats.scanned == 2
        assert stats.finalized == 2
        assert stats.failed == 0
        assert len(finalize_calls) == 2
        assert tenant1 in finalize_calls
        assert tenant2 in finalize_calls

    async def test_run_skips_pending_within_24h(self, monkeypatch: Any) -> None:
        """Mock SELECT returns 0 (already filtered) → stats {scanned:0, finalized:0, failed:0}."""
        fake_conn = AsyncMock()
        fake_conn.fetch.return_value = []

        @asynccontextmanager
        async def fake_system_tx(pool: Any) -> AsyncGenerator[Any, None]:
            yield fake_conn

        monkeypatch.setattr(
            "mem_mcp.jobs.retention_deletion.system_tx",
            fake_system_tx,
        )

        pool = AsyncMock()
        deleter = FakeCognitoAdminDeleter()
        sign_outter = FakeCognitoGlobalSignOutter()
        client_deleter = FakeCognitoClientDeleter()
        audit = FakeAuditLogger()

        job = RetentionDeletionJob(pool, deleter, sign_outter, client_deleter, audit)
        stats = await job.run(request_id="test-batch-2")

        assert stats.scanned == 0
        assert stats.finalized == 0
        assert stats.failed == 0

    async def test_run_one_failure_continues_batch(self, monkeypatch: Any) -> None:
        """finalize_closure raises for first tenant → second still finalized → stats {scanned:2, finalized:1, failed:1}."""
        tenant1 = uuid4()
        tenant2 = uuid4()

        fake_conn = AsyncMock()
        fake_conn.fetch.return_value = [
            {"id": tenant1},
            {"id": tenant2},
        ]

        @asynccontextmanager
        async def fake_system_tx(pool: Any) -> AsyncGenerator[Any, None]:
            yield fake_conn

        finalize_calls = []

        async def fake_finalize_closure(
            pool: Any,
            *,
            tenant_id: Any,
            deleter: Any,
            sign_outter: Any,
            client_deleter: Any,
            audit: Any,
            request_id: Any,
        ) -> None:
            finalize_calls.append(tenant_id)
            if tenant_id == tenant1:
                raise RuntimeError("First tenant failed")

        monkeypatch.setattr(
            "mem_mcp.jobs.retention_deletion.system_tx",
            fake_system_tx,
        )
        monkeypatch.setattr(
            "mem_mcp.jobs.retention_deletion.finalize_closure",
            fake_finalize_closure,
        )

        pool = AsyncMock()
        deleter = FakeCognitoAdminDeleter()
        sign_outter = FakeCognitoGlobalSignOutter()
        client_deleter = FakeCognitoClientDeleter()
        audit = FakeAuditLogger()

        job = RetentionDeletionJob(pool, deleter, sign_outter, client_deleter, audit)
        stats = await job.run(request_id="test-batch-3")

        assert stats.scanned == 2
        assert stats.finalized == 1
        assert stats.failed == 1
        assert len(finalize_calls) == 2  # both were attempted
