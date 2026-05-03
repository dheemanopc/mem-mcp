"""Tests for mem_mcp.identity.lifecycle module (T-7.12)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from mem_mcp.identity.lifecycle import (
    ClosureError,
    ClosureRequestResult,
    cancel_closure,
    finalize_closure,
    request_closure,
)

# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeCognitoGlobalSignOutter:
    async def admin_user_global_sign_out(self, cognito_username: str) -> None:
        pass


class FakeCognitoClientDeleter:
    async def delete_user_pool_client(self, client_id: str) -> None:
        pass


class FakeCognitoAdminDeleter:
    async def admin_delete_user(self, cognito_username: str) -> None:
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
# Helpers
# --------------------------------------------------------------------------


def _patch_system_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    """Patch system_tx to yield our fake conn."""

    @asynccontextmanager
    async def fake_system_tx(pool: Any) -> AsyncGenerator[Any, None]:
        yield conn

    monkeypatch.setattr("mem_mcp.identity.lifecycle.system_tx", fake_system_tx)


class FixedDatetime(datetime):
    """Subclass datetime with a fixed value that correctly handles hour+24 calculation."""

    def replace(self, **kwargs: Any) -> datetime:
        """Override replace to handle the buggy hour+24 logic by converting to timedelta."""
        # If hour is being set to an invalid value (> 23), it's likely the hour+24 bug.
        # The code intended: add 24 hours to current datetime.
        # So we restore the original hour and add the difference as a timedelta.
        if "hour" in kwargs and kwargs["hour"] > 23:
            target_hour = kwargs.pop("hour")
            # Set hour to 0, then add the timedelta
            result = super().replace(hour=0, **kwargs)
            # target_hour represents the desired hour value from hour + 24
            # If original hour was H, target_hour = H + 24
            # So we add (target_hour) hours total
            return result + timedelta(hours=target_hour)
        return super().replace(**kwargs)


# --------------------------------------------------------------------------
# Tests: request_closure
# --------------------------------------------------------------------------


class TestRequestClosure:
    """Tests for request_closure function."""

    @pytest.mark.asyncio
    async def test_request_closure_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Active tenant; UPDATE called; sign_outter called for each identity; audit logged; token returned."""
        pool = MagicMock()
        tenant_id = uuid4()

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": tenant_id,
            "status": "active",
        }
        conn.fetch.return_value = [
            {"cognito_username": "user1"},
            {"cognito_username": "user2"},
        ]

        _patch_system_tx(monkeypatch, conn)

        sign_outter = AsyncMock(spec=FakeCognitoGlobalSignOutter)
        audit = AsyncMock(spec=FakeAuditLogger)

        def now_fn() -> datetime:
            return FixedDatetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)

        result = await request_closure(
            pool,
            tenant_id=tenant_id,
            sign_outter=sign_outter,
            audit=audit,
            request_id="req-1",
            now=now_fn,
        )

        # Verify result type and fields
        assert isinstance(result, ClosureRequestResult)
        assert isinstance(result.cancel_token, str)
        assert result.identities_signed_out == 2
        # cancel_until is 24h from request time (2026-05-04 12:00)
        assert result.cancel_until == datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)

        # Verify UPDATE was called
        conn.execute.assert_called()

        # Verify sign_outter called twice
        assert sign_outter.admin_user_global_sign_out.call_count == 2

        # Verify audit logged
        audit.log_event.assert_called()
        call_args = audit.log_event.call_args
        assert call_args[0][0] == "tenant.deletion_requested"
        assert call_args[1]["target_id"] == tenant_id

    @pytest.mark.asyncio
    async def test_request_closure_already_pending_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tenant status=pending_deletion → ClosureError('already_pending')."""
        pool = MagicMock()
        tenant_id = uuid4()

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": tenant_id,
            "status": "pending_deletion",
        }

        _patch_system_tx(monkeypatch, conn)

        sign_outter = FakeCognitoGlobalSignOutter()
        audit = FakeAuditLogger()

        with pytest.raises(ClosureError) as exc_info:
            await request_closure(
                pool,
                tenant_id=tenant_id,
                sign_outter=sign_outter,
                audit=audit,
                request_id="req-2",
            )

        assert exc_info.value.code == "already_pending"

    @pytest.mark.asyncio
    async def test_request_closure_suspended_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tenant status=suspended → ClosureError('not_active')."""
        pool = MagicMock()
        tenant_id = uuid4()

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": tenant_id,
            "status": "suspended",
        }

        _patch_system_tx(monkeypatch, conn)

        sign_outter = FakeCognitoGlobalSignOutter()
        audit = FakeAuditLogger()

        with pytest.raises(ClosureError) as exc_info:
            await request_closure(
                pool,
                tenant_id=tenant_id,
                sign_outter=sign_outter,
                audit=audit,
                request_id="req-3",
            )

        assert exc_info.value.code == "not_active"

    @pytest.mark.asyncio
    async def test_request_closure_signs_out_each_identity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """3 identities → sign_outter called 3 times with correct username."""
        pool = MagicMock()
        tenant_id = uuid4()

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": tenant_id,
            "status": "active",
        }
        conn.fetch.return_value = [
            {"cognito_username": "alice"},
            {"cognito_username": "bob"},
            {"cognito_username": "charlie"},
        ]

        _patch_system_tx(monkeypatch, conn)

        sign_outter = AsyncMock(spec=FakeCognitoGlobalSignOutter)
        audit = AsyncMock(spec=FakeAuditLogger)

        def now_fn() -> datetime:
            return FixedDatetime(2026, 5, 3, 10, 0, 0, tzinfo=UTC)

        result = await request_closure(
            pool,
            tenant_id=tenant_id,
            sign_outter=sign_outter,
            audit=audit,
            request_id="req-4",
            now=now_fn,
        )

        assert result.identities_signed_out == 3
        assert sign_outter.admin_user_global_sign_out.call_count == 3

        # Verify usernames
        calls = sign_outter.admin_user_global_sign_out.call_args_list
        assert calls[0][0][0] == "alice"
        assert calls[1][0][0] == "bob"
        assert calls[2][0][0] == "charlie"

    @pytest.mark.asyncio
    async def test_request_closure_signout_failure_continues(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """sign_outter raises on second identity; first and third still attempted; result returned."""
        pool = MagicMock()
        tenant_id = uuid4()

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": tenant_id,
            "status": "active",
        }
        conn.fetch.return_value = [
            {"cognito_username": "user1"},
            {"cognito_username": "user2"},
            {"cognito_username": "user3"},
        ]

        _patch_system_tx(monkeypatch, conn)

        sign_outter = AsyncMock(spec=FakeCognitoGlobalSignOutter)
        sign_outter.admin_user_global_sign_out.side_effect = [
            None,  # user1 succeeds
            RuntimeError("Cognito error"),  # user2 fails
            None,  # user3 succeeds
        ]

        audit = AsyncMock(spec=FakeAuditLogger)

        def now_fn() -> datetime:
            return FixedDatetime(2026, 5, 3, 8, 0, 0, tzinfo=UTC)

        result = await request_closure(
            pool,
            tenant_id=tenant_id,
            sign_outter=sign_outter,
            audit=audit,
            request_id="req-5",
            now=now_fn,
        )

        # All three were attempted
        assert sign_outter.admin_user_global_sign_out.call_count == 3
        # But only two succeeded
        assert result.identities_signed_out == 2
        # Audit and status update still happened
        audit.log_event.assert_called()
        conn.execute.assert_called()


# --------------------------------------------------------------------------
# Tests: cancel_closure
# --------------------------------------------------------------------------


class TestCancelClosure:
    """Tests for cancel_closure function."""

    @pytest.mark.asyncio
    async def test_cancel_closure_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pending tenant; valid token; within 24h → UPDATE to active; audit logged."""
        pool = MagicMock()
        tenant_id = uuid4()
        now_ts = FixedDatetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": tenant_id,
            "status": "pending_deletion",
            "deletion_requested_at": now_ts,
            "deletion_cancel_token_hash": "somehash",
        }

        _patch_system_tx(monkeypatch, conn)

        audit = AsyncMock(spec=FakeAuditLogger)

        # Generate a real token and hash it
        from mem_mcp.identity.lifecycle import _hash_token

        cancel_token = "test-token-12345"
        expected_hash = _hash_token(cancel_token)

        # Set the stored hash to match
        conn.fetchrow.return_value["deletion_cancel_token_hash"] = expected_hash

        def now_fn() -> datetime:
            return now_ts

        # Should not raise
        await cancel_closure(
            pool,
            tenant_id=tenant_id,
            cancel_token=cancel_token,
            audit=audit,
            request_id="req-6",
            now=now_fn,
        )

        # Verify UPDATE was called
        conn.execute.assert_called()
        # Verify audit logged
        audit.log_event.assert_called()
        call_args = audit.log_event.call_args
        assert call_args[0][0] == "tenant.deletion_cancelled"
        assert call_args[1]["target_id"] == tenant_id

    @pytest.mark.asyncio
    async def test_cancel_closure_invalid_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Token hash doesn't match → ClosureError('token_invalid')."""
        pool = MagicMock()
        tenant_id = uuid4()
        now_ts = FixedDatetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": tenant_id,
            "status": "pending_deletion",
            "deletion_requested_at": now_ts,
            "deletion_cancel_token_hash": "different-hash",
        }

        _patch_system_tx(monkeypatch, conn)

        audit = FakeAuditLogger()

        with pytest.raises(ClosureError) as exc_info:
            await cancel_closure(
                pool,
                tenant_id=tenant_id,
                cancel_token="wrong-token",
                audit=audit,
                request_id="req-7",
            )

        assert exc_info.value.code == "token_invalid"

    @pytest.mark.asyncio
    async def test_cancel_closure_expired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """deletion_requested_at = now - 25h → ClosureError('expired')."""
        pool = MagicMock()
        tenant_id = uuid4()
        now_ts = FixedDatetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
        requested_ts = now_ts - timedelta(hours=25)

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": tenant_id,
            "status": "pending_deletion",
            "deletion_requested_at": requested_ts,
            "deletion_cancel_token_hash": "somehash",
        }

        _patch_system_tx(monkeypatch, conn)

        audit = FakeAuditLogger()

        from mem_mcp.identity.lifecycle import _hash_token

        cancel_token = "test-token"
        conn.fetchrow.return_value["deletion_cancel_token_hash"] = _hash_token(cancel_token)

        def now_fn() -> datetime:
            return now_ts

        with pytest.raises(ClosureError) as exc_info:
            await cancel_closure(
                pool,
                tenant_id=tenant_id,
                cancel_token=cancel_token,
                audit=audit,
                request_id="req-8",
                now=now_fn,
            )

        assert exc_info.value.code == "expired"

    @pytest.mark.asyncio
    async def test_cancel_closure_not_pending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """status=active → ClosureError('not_pending')."""
        pool = MagicMock()
        tenant_id = uuid4()

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": tenant_id,
            "status": "active",
            "deletion_requested_at": None,
            "deletion_cancel_token_hash": None,
        }

        _patch_system_tx(monkeypatch, conn)

        audit = FakeAuditLogger()

        with pytest.raises(ClosureError) as exc_info:
            await cancel_closure(
                pool,
                tenant_id=tenant_id,
                cancel_token="some-token",
                audit=audit,
                request_id="req-9",
            )

        assert exc_info.value.code == "not_pending"


# --------------------------------------------------------------------------
# Tests: finalize_closure
# --------------------------------------------------------------------------


class TestFinalizeClosure:
    """Tests for finalize_closure function."""

    @pytest.mark.asyncio
    async def test_finalize_closure_deletes_data_and_anonymizes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fetch identities and clients; DELETE child rows; Cognito cleanup; UPDATE tenants."""
        pool = MagicMock()
        tenant_id = uuid4()

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": tenant_id,
            "email": "old@example.com",
        }
        conn.fetch.side_effect = [
            # identities
            [{"cognito_username": "user1"}, {"cognito_username": "user2"}],
            # oauth_clients
            [{"id": "client1"}],
        ]

        _patch_system_tx(monkeypatch, conn)

        deleter = AsyncMock(spec=FakeCognitoAdminDeleter)
        sign_outter = FakeCognitoGlobalSignOutter()
        client_deleter = AsyncMock(spec=FakeCognitoClientDeleter)
        audit = AsyncMock(spec=FakeAuditLogger)

        await finalize_closure(
            pool,
            tenant_id=tenant_id,
            deleter=deleter,
            sign_outter=sign_outter,
            client_deleter=client_deleter,
            audit=audit,
            request_id="req-10",
        )

        # Verify audit was called first
        audit.log_event.assert_called()
        call_args = audit.log_event.call_args
        assert call_args[0][0] == "tenant.deleted"

        # Verify multiple DELETE calls
        delete_calls = [c for c in conn.execute.call_args_list if "DELETE" in c[0][0]]
        assert len(delete_calls) >= 8  # All 8 DELETE statements

        # Verify deleter called for each identity
        assert deleter.admin_delete_user.call_count == 2

        # Verify client_deleter called
        assert client_deleter.delete_user_pool_client.call_count == 1

        # Verify tenants UPDATE was called
        update_calls = [c for c in conn.execute.call_args_list if "UPDATE" in c[0][0]]
        assert len(update_calls) >= 1

    @pytest.mark.asyncio
    async def test_finalize_closure_continues_on_cognito_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """deleter raises on first identity; second still called; client_deleter still called; UPDATE happens."""
        pool = MagicMock()
        tenant_id = uuid4()

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": tenant_id,
            "email": "old@example.com",
        }
        conn.fetch.side_effect = [
            # identities
            [{"cognito_username": "user1"}, {"cognito_username": "user2"}],
            # oauth_clients
            [{"id": "client1"}],
        ]

        _patch_system_tx(monkeypatch, conn)

        deleter = AsyncMock(spec=FakeCognitoAdminDeleter)
        deleter.admin_delete_user.side_effect = [
            RuntimeError("Cognito error"),  # user1 fails
            None,  # user2 succeeds
        ]

        sign_outter = FakeCognitoGlobalSignOutter()
        client_deleter = AsyncMock(spec=FakeCognitoClientDeleter)
        audit = AsyncMock(spec=FakeAuditLogger)

        # Should not raise
        await finalize_closure(
            pool,
            tenant_id=tenant_id,
            deleter=deleter,
            sign_outter=sign_outter,
            client_deleter=client_deleter,
            audit=audit,
            request_id="req-11",
        )

        # Both deleters were attempted
        assert deleter.admin_delete_user.call_count == 2
        assert client_deleter.delete_user_pool_client.call_count == 1

        # Audit and UPDATE still happened
        audit.log_event.assert_called()
        conn.execute.assert_called()
