"""Nightly retention deletion job (T-7.12).

Scans for tenants in pending_deletion status past the 24h grace window
and finalizes their account closure.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from mem_mcp.db import system_tx
from mem_mcp.identity.lifecycle import finalize_closure
from mem_mcp.logging_setup import get_logger, setup_logging

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


_log = get_logger("mem_mcp.jobs.retention_deletion")

GRACE_PERIOD_HOURS = 24


# --------------------------------------------------------------------------
# Protocol seams
# --------------------------------------------------------------------------


class CognitoAdminDeleter(Protocol):
    """Wraps AdminDeleteUser."""

    async def admin_delete_user(self, cognito_username: str) -> None: ...


class CognitoGlobalSignOutter(Protocol):
    """Wraps AdminUserGlobalSignOut."""

    async def admin_user_global_sign_out(self, cognito_username: str) -> None: ...


class CognitoClientDeleter(Protocol):
    """Wraps DeleteUserPoolClient."""

    async def delete_user_pool_client(self, client_id: str) -> None: ...


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RetentionDeletionStats:
    scanned: int
    finalized: int
    failed: int


# --------------------------------------------------------------------------
# Job class
# --------------------------------------------------------------------------


class RetentionDeletionJob:
    """Cron job: finalize closure for tenants whose 24h grace has expired."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        deleter: CognitoAdminDeleter,
        sign_outter: CognitoGlobalSignOutter,
        client_deleter: CognitoClientDeleter,
        audit: Any,  # AuditLogger Protocol
    ) -> None:
        self._pool = pool
        self._deleter = deleter
        self._sign_outter = sign_outter
        self._client_deleter = client_deleter
        self._audit = audit

    async def run(self, request_id: str = "batch-retention-deletion") -> RetentionDeletionStats:
        """Find pending_deletion tenants past grace window and finalize them.

        SELECT id FROM tenants WHERE status='pending_deletion'
          AND deletion_requested_at < now()-interval '24 hours'.
        For each id, call finalize_closure.
        Catch + log per-tenant errors so one failure doesn't abort the batch.
        Return stats: scanned, finalized, failed.
        """
        async with system_tx(self._pool) as conn:
            candidate_rows = await conn.fetch(
                f"""
                SELECT id FROM tenants
                WHERE status = 'pending_deletion'
                  AND deletion_requested_at < now() - interval '{GRACE_PERIOD_HOURS} hours'
                ORDER BY deletion_requested_at ASC
                """
            )

        candidates = [row["id"] for row in candidate_rows]
        scanned = len(candidates)
        finalized = 0
        failed = 0

        _log.info(
            f"Found {scanned} tenants past grace window for finalization",
            extra={"request_id": request_id},
        )

        for tenant_id in candidates:
            try:
                await finalize_closure(
                    self._pool,
                    tenant_id=tenant_id,
                    deleter=self._deleter,
                    sign_outter=self._sign_outter,
                    client_deleter=self._client_deleter,
                    audit=self._audit,
                    request_id=request_id,
                )
                finalized += 1
                _log.info(
                    f"Finalized closure for tenant {tenant_id}", extra={"request_id": request_id}
                )
            except Exception as exc:
                failed += 1
                _log.error(
                    f"Failed to finalize closure for tenant {tenant_id}",
                    exc_info=exc,
                    extra={"request_id": request_id},
                )

        _log.info(
            f"Retention deletion job complete: scanned={scanned}, finalized={finalized}, failed={failed}",
            extra={"request_id": request_id},
        )

        return RetentionDeletionStats(scanned=scanned, finalized=finalized, failed=failed)


# --------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------


async def main() -> None:
    """Run retention deletion job standalone."""
    parser = argparse.ArgumentParser(description="Retention deletion job")
    parser.parse_args()

    setup_logging()

    # This would normally wire up real Cognito clients; for now just log.
    _log.info("Retention deletion job not fully wired in this entry point")


if __name__ == "__main__":
    asyncio.run(main())
