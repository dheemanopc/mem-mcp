"""Tests for mem_mcp.jobs.reconcile_signup_backlog."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from mem_mcp.jobs.reconcile_signup_backlog import (
    BacklogReport,
    reconcile_signup_backlog,
)
from mem_mcp.web.signup_requests.repository import StuckAwaitingRequest

_REPO = "mem_mcp.web.signup_requests.repository"
_MOD = "mem_mcp.jobs.reconcile_signup_backlog"


def create_mock_system_tx(mock_conn: AsyncMock) -> object:
    """Create a mock system_tx that yields the given connection."""

    @asynccontextmanager
    async def _mock_system_tx(pool: object) -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    return _mock_system_tx


class RecordingMailer:
    """SignupMailer that records calls instead of sending."""

    def __init__(self) -> None:
        self.verifications: list[tuple[str, str]] = []
        self.digests: list[dict[str, object]] = []

    async def send_verification(self, *, to: str, verify_url: str) -> None:
        self.verifications.append((to, verify_url))

    async def send_backlog_digest(
        self,
        *,
        operator_email: str,
        awaiting_total: int,
        reminded: list[str],
        expired: list[str],
    ) -> None:
        self.digests.append(
            {
                "operator_email": operator_email,
                "awaiting_total": awaiting_total,
                "reminded": list(reminded),
                "expired": list(expired),
            }
        )


def _stuck(email: str, reminder_count: int = 0) -> StuckAwaitingRequest:
    return StuckAwaitingRequest(
        id=uuid4(),
        email=email,
        name="Someone",
        submitted_at=datetime(2026, 8, 9, tzinfo=UTC),
        verify_expires_at=datetime(2026, 8, 10, tzinfo=UTC),
        reminder_count=reminder_count,
    )


@pytest.mark.asyncio
async def test_dry_run_mutates_nothing_and_sends_no_email() -> None:
    """Dry run reports would-be actions but issues no reminder and no email."""
    mailer = RecordingMailer()
    with (
        patch(f"{_REPO}.expire_stale_awaiting", AsyncMock(return_value=[])),
        patch(f"{_REPO}.list_stuck_awaiting", AsyncMock(return_value=[_stuck("a@x.com")])),
        patch(f"{_REPO}.issue_verification_reminder", AsyncMock()) as issue,
        patch(f"{_REPO}.count_by_status", AsyncMock(return_value=1)),
    ):
        report = await reconcile_signup_backlog(
            AsyncMock(), mailer, AsyncMock(), operator_email="op@x.com", dry_run=True
        )

    assert report.reminded == ["a@x.com"]
    assert report.expired == []
    issue.assert_not_awaited()
    assert mailer.verifications == []
    assert mailer.digests == []


@pytest.mark.asyncio
async def test_expires_reminds_and_digests_operator() -> None:
    """Real run expires stale, resends verification, and digests the operator once."""
    mailer = RecordingMailer()
    mock_conn = AsyncMock()
    mock_audit = AsyncMock()
    with (
        patch(f"{_REPO}.expire_stale_awaiting", AsyncMock(return_value=["gone@x.com"])),
        patch(f"{_REPO}.list_stuck_awaiting", AsyncMock(return_value=[_stuck("a@x.com")])),
        patch(f"{_REPO}.issue_verification_reminder", AsyncMock(return_value="rawtoken123")),
        patch(f"{_REPO}.count_by_status", AsyncMock(return_value=1)),
        patch(f"{_MOD}.system_tx", create_mock_system_tx(mock_conn)),
    ):
        report = await reconcile_signup_backlog(
            AsyncMock(), mailer, mock_audit, operator_email="op@x.com", dry_run=False
        )

    assert report.expired == ["gone@x.com"]
    assert report.reminded == ["a@x.com"]
    assert report.actioned == 2
    # Verification email carries the fresh token in the verify URL.
    assert len(mailer.verifications) == 1
    to, url = mailer.verifications[0]
    assert to == "a@x.com"
    assert "rawtoken123" in url
    # Exactly one operator digest, with both buckets.
    assert len(mailer.digests) == 1
    assert mailer.digests[0]["expired"] == ["gone@x.com"]
    assert mailer.digests[0]["reminded"] == ["a@x.com"]
    # Audit records the reminder with a 1-based reminder number.
    assert mock_audit.audit.call_args[1]["action"] == "signup.verification_reminded"
    assert mock_audit.audit.call_args[1]["details"]["reminder_number"] == 1


@pytest.mark.asyncio
async def test_no_change_sends_no_digest() -> None:
    """Steady state (nothing expired, nobody due) sends no operator digest."""
    mailer = RecordingMailer()
    with (
        patch(f"{_REPO}.expire_stale_awaiting", AsyncMock(return_value=[])),
        patch(f"{_REPO}.list_stuck_awaiting", AsyncMock(return_value=[])),
        patch(f"{_REPO}.issue_verification_reminder", AsyncMock()),
        patch(f"{_REPO}.count_by_status", AsyncMock(return_value=0)),
    ):
        report = await reconcile_signup_backlog(
            AsyncMock(), mailer, AsyncMock(), operator_email="op@x.com", dry_run=False
        )

    assert report.actioned == 0
    assert mailer.digests == []
    assert mailer.verifications == []


@pytest.mark.asyncio
async def test_raced_reminder_is_skipped_not_emailed() -> None:
    """If the row left awaiting_verification between SELECT and UPDATE, skip it."""
    mailer = RecordingMailer()
    with (
        patch(f"{_REPO}.expire_stale_awaiting", AsyncMock(return_value=[])),
        patch(f"{_REPO}.list_stuck_awaiting", AsyncMock(return_value=[_stuck("race@x.com")])),
        patch(f"{_REPO}.issue_verification_reminder", AsyncMock(return_value=None)),
        patch(f"{_REPO}.count_by_status", AsyncMock(return_value=0)),
    ):
        report = await reconcile_signup_backlog(
            AsyncMock(), mailer, AsyncMock(), operator_email="op@x.com", dry_run=False
        )

    assert report.skipped_raced == ["race@x.com"]
    assert report.reminded == []
    assert mailer.verifications == []
    # Nothing actually changed → no digest.
    assert mailer.digests == []


def test_report_actioned_counts_expired_plus_reminded() -> None:
    report = BacklogReport(expired=["a@x.com", "b@x.com"], reminded=["c@x.com"])
    assert report.actioned == 3
