"""SES helpers for signup-flow emails. Async wrappers around boto3 ses."""

from __future__ import annotations

import asyncio

import boto3  # type: ignore[import-untyped]

from mem_mcp.config import get_settings
from mem_mcp.logging_setup import get_logger

_log = get_logger("mem_mcp.signup_requests.emails")


def _ses_client():  # type: ignore[no-untyped-def]
    """Lazy SES client. Constructed once per process via lru_cache pattern."""
    return boto3.client("ses", region_name=get_settings().region)


async def _send(to: str, subject: str, body_text: str, body_html: str | None = None) -> None:
    """Send a single SES email. Non-fatal on failure (logs + swallows)."""
    s = get_settings()
    sender = s.ses_from

    def _do() -> None:
        client = _ses_client()  # type: ignore[no-untyped-call]
        message: dict[str, dict] = {  # type: ignore[type-arg]
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": body_text, "Charset": "UTF-8"}},
        }
        if body_html:
            message["Body"]["Html"] = {"Data": body_html, "Charset": "UTF-8"}
        client.send_email(
            Source=sender,
            Destination={"ToAddresses": [to]},
            Message=message,
        )

    try:
        await asyncio.to_thread(_do)
        _log.info("ses_email_sent", to=to, subject=subject)
    except Exception as exc:
        _log.warning("ses_email_failed", to=to, subject=subject, error=str(exc))


async def send_verification_email(*, to: str, verify_url: str) -> None:
    body = (
        f"Hi,\n\n"
        f"You requested access to mem-mcp. To complete your request, "
        f"please verify your email by clicking the link below:\n\n"
        f"{verify_url}\n\n"
        f"This link expires in 24 hours. If you didn't request access, ignore this email.\n\n"
        f"— mem-mcp"
    )
    await _send(to=to, subject="Verify your mem-mcp access request", body_text=body)


async def send_operator_notification(
    *, operator_email: str, applicant_email: str, applicant_name: str | None
) -> None:
    body = (
        f"A new mem-mcp access request is ready for review.\n\n"
        f"Email: {applicant_email}\n"
        f"Name: {applicant_name or '(not provided)'}\n\n"
        f"Review at: https://memapp.dheemantech.in/admin/signup-requests\n"
    )
    await _send(to=operator_email, subject="New mem-mcp access request", body_text=body)


async def send_approval_email(*, to: str) -> None:
    body = (
        "Hi,\n\n"
        "Your mem-mcp access has been approved. You can sign in here:\n\n"
        "https://memapp.dheemantech.in/auth/login\n\n"
        "After signing in, see the docs at https://memapp.dheemantech.in/docs to connect "
        "Claude Code, Claude.ai, or ChatGPT to your memory.\n\n"
        "— mem-mcp"
    )
    await _send(to=to, subject="Your mem-mcp access has been approved", body_text=body)


async def send_rejection_email(*, to: str, notes: str | None) -> None:
    notes_para = f"\n{notes}\n" if notes else ""
    body = (
        "Hi,\n\n"
        "Thanks for your interest in mem-mcp. We're not approving access at this time.\n"
        f"{notes_para}\n"
        "You're welcome to apply again later.\n\n"
        "— mem-mcp"
    )
    await _send(to=to, subject="Your mem-mcp access request", body_text=body)
