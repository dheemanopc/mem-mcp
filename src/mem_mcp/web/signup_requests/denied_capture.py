"""Capture Google sign-ins that were denied for not being invited.

A user who clicks "Sign in with Google" but is not on the ``invited_emails``
allowlist is denied at the Cognito PreSignUp choke point — no Cognito user and
no ``signup_requests`` row are created, so the operator never sees that someone
tried (BUG 2026-08-09). This recorder is the seam the ``/internal/check_invite``
endpoint calls to drop such a denial into the review queue as a ``pending``
request, so the operator can approve it the same way as a form submission.

Best-effort by contract: recording must never change the allow/deny decision or
add latency-fatal failure to the auth path, so the endpoint wraps every call in
a swallow-and-log guard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mem_mcp.audit.logger import AuditLogger
from mem_mcp.db import system_tx
from mem_mcp.logging_setup import get_logger
from mem_mcp.web.signup_requests import emails, repository

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

_log = get_logger("mem_mcp.signup_requests.denied_capture")


class SignupQueueRecorder:
    """Records not-invited Google denials into the signup review queue."""

    def __init__(self, *, pool: asyncpg.Pool, audit: AuditLogger, operator_email: str) -> None:
        self._pool = pool
        self._audit = audit
        self._operator_email = operator_email

    async def record(self, *, email: str, provider: str | None) -> None:
        """Upsert a 'pending' signup_requests row for a denied login.

        Idempotent per email; leaves already-reviewed requests untouched. On a
        brand-new capture it audits and notifies the operator; repeated denied
        clicks by the same person are no-ops (no duplicate row, alert, or audit).
        """
        status, inserted = await repository.record_verified_access_request(self._pool, email=email)
        _log.info(
            "denied_login_captured",
            provider=provider,
            status=status,
            inserted=inserted,
        )
        if not inserted:
            return
        async with system_tx(self._pool) as conn:
            await self._audit.audit(
                conn,
                action="signup.captured_from_denied_login",
                result="success",
                tenant_id=None,
                identity_id=None,
                client_id="internal-check-invite",
                target_kind="signup_request",
                details={"email": email, "provider": provider, "source": "google_denied"},
            )
        # Immediate operator alert — mirrors the form path's verify-time notify,
        # so a blocked Google sign-in reaches the operator without waiting for
        # the hourly backlog digest. send_operator_notification is best-effort.
        await emails.send_operator_notification(
            operator_email=self._operator_email,
            applicant_email=email,
            applicant_name=None,
        )
