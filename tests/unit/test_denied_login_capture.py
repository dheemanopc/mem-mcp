"""Tests for capturing not-invited Google denials into the signup review queue."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from mem_mcp.web.signup_requests.denied_capture import SignupQueueRecorder

_REPO = "mem_mcp.web.signup_requests.repository.record_verified_access_request"
_MOD = "mem_mcp.web.signup_requests.denied_capture"


def _mock_system_tx(mock_conn: AsyncMock) -> object:
    @asynccontextmanager
    async def _tx(pool: object) -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    return _tx


@pytest.mark.asyncio
async def test_new_denial_is_queued_audited_and_operator_notified() -> None:
    """A first-time denial inserts a pending row, audits, and alerts the operator."""
    audit = AsyncMock()
    mock_conn = AsyncMock()
    recorder = SignupQueueRecorder(pool=AsyncMock(), audit=audit, operator_email="op@x.com")

    with (
        patch(_REPO, AsyncMock(return_value=("pending", True))),
        patch(f"{_MOD}.system_tx", _mock_system_tx(mock_conn)),
        patch(f"{_MOD}.emails.send_operator_notification", AsyncMock()) as notify,
    ):
        await recorder.record(email="New@X.com", provider="google")

    audit_call = audit.audit.call_args
    assert audit_call is not None
    assert audit_call[1]["action"] == "signup.captured_from_denied_login"
    assert audit_call[1]["details"]["source"] == "google_denied"
    notify.assert_awaited_once()
    notify_call = notify.await_args
    assert notify_call is not None
    assert notify_call[1]["applicant_email"] == "New@X.com"
    assert notify_call[1]["operator_email"] == "op@x.com"


@pytest.mark.asyncio
async def test_duplicate_denial_is_noop_no_audit_no_notify() -> None:
    """A repeat denial (row already present) neither audits nor re-notifies."""
    audit = AsyncMock()
    recorder = SignupQueueRecorder(pool=AsyncMock(), audit=audit, operator_email="op@x.com")

    with (
        patch(_REPO, AsyncMock(return_value=("pending", False))),
        patch(f"{_MOD}.emails.send_operator_notification", AsyncMock()) as notify,
    ):
        await recorder.record(email="dupe@x.com", provider="google")

    audit.audit.assert_not_called()
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_recorder_error_propagates_for_endpoint_to_swallow() -> None:
    """The recorder does not swallow errors itself — the endpoint guards the call.

    This keeps the recorder simple/testable; the auth path wraps record() in a
    try/except so a DB blip never changes the allow/deny decision.
    """
    recorder = SignupQueueRecorder(pool=AsyncMock(), audit=AsyncMock(), operator_email="op@x.com")
    with patch(_REPO, AsyncMock(side_effect=RuntimeError("db down"))):
        with pytest.raises(RuntimeError, match="db down"):
            await recorder.record(email="err@x.com", provider="google")
