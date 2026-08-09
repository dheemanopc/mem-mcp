"""Periodic job to clear the signup-verification backlog.

Background (BUG 2026-08-09 "admin signup queue shows empty while applicants are
blocked"): a signup request only reaches the operator's review queue once the
applicant clicks the SES verification link (``awaiting_verification -> pending``).
Applicants who never click — spam-foldered mail, expired token, missed link —
sit invisibly in ``awaiting_verification`` forever. From the operator's seat this
is indistinguishable from "nobody applied", so the backlog silently grows.

This job clears that backlog on every run by:

  1. Expiring stale rows whose verify token has lapsed (``awaiting_verification``
     -> ``expired``), so dead requests stop hiding in limbo.
  2. Re-sending a fresh verification email to still-live stuck requests, capped
     and cooldown-throttled so nobody is spammed. A click unblocks them into the
     ``pending`` queue where the operator can approve.
  3. Emailing the operator a digest whenever the backlog changed this run, so
     pre-verification applicants are never invisible again.

Runs via systemd timer / the container scheduler. Also invocable manually:
  python -m mem_mcp.jobs reconcile_signup_backlog [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from mem_mcp.audit.logger import AuditLogger, DbAuditLogger
from mem_mcp.config import get_settings
from mem_mcp.db.tenant_tx import system_tx
from mem_mcp.logging_setup import get_logger, setup_logging
from mem_mcp.web.signup_requests import emails, repository

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

_log = get_logger("mem_mcp.jobs.reconcile_signup_backlog")

# Tuning knobs. Grace before the first nudge (the original email deserves a fair
# shot), cooldown between nudges, and a hard cap so a truly-abandoned request is
# nudged a few times and then left to expire rather than pestered forever.
MIN_AGE_MINUTES = 30
REMINDER_COOLDOWN_HOURS = 24
MAX_REMINDERS = 3
VERIFY_URL_TEMPLATE = "https://memapp.dheemantech.in/auth/verify-email?token={token}"


@dataclass
class BacklogReport:
    """Results from a backlog-reconciliation run."""

    expired: list[str] = field(default_factory=list)
    reminded: list[str] = field(default_factory=list)
    skipped_raced: list[str] = field(default_factory=list)
    awaiting_total: int = 0

    @property
    def actioned(self) -> int:
        """Number of rows this run changed (expired + reminded)."""
        return len(self.expired) + len(self.reminded)


class SignupMailer(Protocol):
    """Outbound email surface for the backlog job (injected for testability)."""

    async def send_verification(self, *, to: str, verify_url: str) -> None: ...

    async def send_backlog_digest(
        self,
        *,
        operator_email: str,
        awaiting_total: int,
        reminded: list[str],
        expired: list[str],
    ) -> None: ...


class SesMailer:
    """Production mailer backed by the signup_requests SES helpers."""

    async def send_verification(self, *, to: str, verify_url: str) -> None:
        await emails.send_verification_email(to=to, verify_url=verify_url)

    async def send_backlog_digest(
        self,
        *,
        operator_email: str,
        awaiting_total: int,
        reminded: list[str],
        expired: list[str],
    ) -> None:
        await emails.send_backlog_digest(
            operator_email=operator_email,
            awaiting_total=awaiting_total,
            reminded=reminded,
            expired=expired,
        )


async def reconcile_signup_backlog(
    pool: asyncpg.Pool,
    mailer: SignupMailer,
    audit: AuditLogger,
    *,
    operator_email: str,
    dry_run: bool = False,
) -> BacklogReport:
    """Expire dead requests, nudge live stuck ones, and digest the operator.

    Args:
        pool: asyncpg pool.
        mailer: outbound email surface (SesMailer in prod).
        audit: audit logger.
        operator_email: recipient for the backlog digest.
        dry_run: if True, compute and log what would happen but mutate nothing
            and send no email.

    Returns:
        BacklogReport with expired, reminded, raced-skip, and awaiting counts.
    """
    _log.info("reconcile_signup_backlog_started", dry_run=dry_run)
    report = BacklogReport()

    # 1) Expire requests whose verification token has lapsed.
    report.expired = await repository.expire_stale_awaiting(pool, dry_run=dry_run)

    # 2) Nudge still-live stuck requests with a fresh verification link.
    candidates = await repository.list_stuck_awaiting(
        pool,
        min_age_minutes=MIN_AGE_MINUTES,
        reminder_cooldown_hours=REMINDER_COOLDOWN_HOURS,
        max_reminders=MAX_REMINDERS,
    )
    for req in candidates:
        if dry_run:
            report.reminded.append(req.email)
            continue
        raw_token = await repository.issue_verification_reminder(pool, request_id=req.id)
        if raw_token is None:
            # Raced with a verify/expire between the SELECT and the UPDATE.
            report.skipped_raced.append(req.email)
            continue
        verify_url = VERIFY_URL_TEMPLATE.format(token=raw_token)
        await mailer.send_verification(to=req.email, verify_url=verify_url)
        report.reminded.append(req.email)
        async with system_tx(pool) as conn:
            await audit.audit(
                conn,
                action="signup.verification_reminded",
                result="success",
                tenant_id=None,
                identity_id=None,
                client_id="job-reconcile-signup-backlog",
                target_id=req.id,
                target_kind="signup_request",
                request_id=str(req.id),
                details={
                    "email": req.email,
                    "reminder_number": req.reminder_count + 1,
                },
            )

    report.awaiting_total = await repository.count_by_status(pool, status="awaiting_verification")

    # 3) Digest the operator only when the backlog actually changed this run.
    #    Steady state (nothing expired, nobody past cooldown) sends nothing, so
    #    the 5-min/hourly cadence never turns into operator spam.
    if not dry_run and (report.expired or report.reminded):
        await mailer.send_backlog_digest(
            operator_email=operator_email,
            awaiting_total=report.awaiting_total,
            reminded=report.reminded,
            expired=report.expired,
        )

    _log.info(
        "reconcile_signup_backlog_done",
        expired_count=len(report.expired),
        reminded_count=len(report.reminded),
        raced_count=len(report.skipped_raced),
        awaiting_total=report.awaiting_total,
        dry_run=dry_run,
    )
    return report


async def main(dry_run: bool = False) -> int:
    """Main entrypoint for the backlog job."""
    setup_logging(get_settings().log_level)
    settings = get_settings()

    import asyncpg

    pool: Any = await asyncpg.create_pool(
        dsn=settings.db_maint_dsn_asyncpg,
        min_size=1,
        max_size=2,
        command_timeout=60,
        server_settings={"application_name": "mem-mcp-reconcile-signup-backlog"},
    )
    try:
        report = await reconcile_signup_backlog(
            pool,
            SesMailer(),
            DbAuditLogger(),
            operator_email=settings.operator_email,
            dry_run=dry_run,
        )
        return report.actioned
    finally:
        await pool.close()


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(prog="reconcile_signup_backlog")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
