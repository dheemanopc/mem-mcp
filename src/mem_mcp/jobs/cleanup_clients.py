"""Daily cleanup of unused/stale OAuth clients.

Per FR-6.5.10:
- never-used: last_used_at IS NULL AND created_at < now() - 24h
- stale:       last_used_at IS NOT NULL AND last_used_at < now() - 90 days

Also cleans up disabled (revoked) clients regardless of age.

Skips the CFT-managed web client (client_name='mem-web-client').
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterable
from typing import TYPE_CHECKING, Protocol

from mem_mcp.config import get_settings
from mem_mcp.db import system_tx
from mem_mcp.logging_setup import get_logger, setup_logging

if TYPE_CHECKING:
    import asyncpg


_log = get_logger("mem_mcp.jobs.cleanup_clients")

NEVER_USED_GRACE_HOURS = 24
STALE_DAYS = 90
PROTECTED_CLIENT_NAMES = ("mem-web-client",)


# --------------------------------------------------------------------------
# Protocol seam (same shape as dcr_admin.CognitoClientDeleter)
# --------------------------------------------------------------------------


class CognitoClientDeleter(Protocol):
    async def delete_user_pool_client(self, client_id: str) -> None: ...


class CandidateLister(Protocol):
    async def list_candidates(self) -> list[dict[str, object]]:
        """Return rows with at least: id, client_name, last_used_at, created_at, disabled."""
        ...


class TombstoneMarker(Protocol):
    async def mark_deleted(self, client_id: str) -> None: ...


# --------------------------------------------------------------------------
# Production implementations
# --------------------------------------------------------------------------


class DbCandidateLister:
    """Production lister: SELECT stale rows from oauth_clients."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_candidates(self) -> list[dict[str, object]]:
        async with system_tx(self._pool) as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, client_name, last_used_at, created_at, disabled
                FROM oauth_clients
                WHERE deleted_at IS NULL
                  AND client_name <> ALL($1::text[])
                  AND (
                       disabled = true
                    OR (last_used_at IS NULL AND created_at < now() - interval '{NEVER_USED_GRACE_HOURS} hours')
                    OR (last_used_at IS NOT NULL AND last_used_at < now() - interval '{STALE_DAYS} days')
                  )
                """,
                list(PROTECTED_CLIENT_NAMES),
            )
        return [dict(r) for r in rows]


class DbTombstoneMarker:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def mark_deleted(self, client_id: str) -> None:
        async with system_tx(self._pool) as conn:
            await conn.execute(
                "UPDATE oauth_clients SET deleted_at = now(), disabled = true WHERE id = $1 AND deleted_at IS NULL",
                client_id,
            )


class BotoCognitoClientDeleter:
    """Reuses the same shape as dcr_admin's deleter; lazy boto3 import."""

    def __init__(self, user_pool_id: str, region: str) -> None:
        self.user_pool_id = user_pool_id
        self.region = region

    async def delete_user_pool_client(self, client_id: str) -> None:
        import asyncio as _aio
        import boto3

        def _call() -> None:
            client = boto3.client("cognito-idp", region_name=self.region)
            try:
                client.delete_user_pool_client(
                    UserPoolId=self.user_pool_id, ClientId=client_id
                )
            except client.exceptions.ResourceNotFoundException:
                # Already gone in Cognito — fine, the job just tombstones locally
                pass

        await _aio.to_thread(_call)


# --------------------------------------------------------------------------
# Job
# --------------------------------------------------------------------------


async def run(
    *,
    lister: CandidateLister,
    cognito_deleter: CognitoClientDeleter,
    tombstone: TombstoneMarker,
    dry_run: bool = False,
) -> int:
    """Run one pass. Return number of rows the job acted on."""
    candidates = await lister.list_candidates()
    _log.info("cleanup_clients_started", candidates=len(candidates), dry_run=dry_run)

    affected = 0
    for row in candidates:
        client_id = str(row["id"])
        reason = _classify(row)
        _log.info(
            "cleanup_clients_candidate",
            client_id=client_id,
            client_name=row.get("client_name"),
            reason=reason,
            dry_run=dry_run,
        )
        if dry_run:
            continue

        # Best-effort Cognito delete
        try:
            await cognito_deleter.delete_user_pool_client(client_id)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "cleanup_clients_cognito_delete_failed",
                client_id=client_id,
                error=str(exc)[:200],
            )
            # Don't tombstone if Cognito delete failed — retry next run
            continue

        # Tombstone local row
        try:
            await tombstone.mark_deleted(client_id)
            affected += 1
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "cleanup_clients_tombstone_failed",
                client_id=client_id,
                error=str(exc)[:200],
            )

    _log.info("cleanup_clients_done", affected=affected, dry_run=dry_run)
    return affected


def _classify(row: dict[str, object]) -> str:
    if row.get("disabled"):
        return "disabled"
    if row.get("last_used_at") is None:
        return "never_used"
    return "stale_90d"


async def main(dry_run: bool = False) -> int:
    """Production entrypoint — wires real DB pool + Cognito client."""
    setup_logging(get_settings().log_level)
    settings = get_settings()

    # Build production pool inline so this script doesn't need a long-lived process pool
    import asyncpg

    pool = await asyncpg.create_pool(
        dsn=settings.db_maint_dsn,
        min_size=1,
        max_size=2,
        command_timeout=30,
        server_settings={"application_name": "mem-mcp-cleanup-clients"},
    )
    try:
        affected = await run(
            lister=DbCandidateLister(pool),
            cognito_deleter=BotoCognitoClientDeleter(
                user_pool_id=settings.cognito_user_pool_id,
                region=settings.region,
            ),
            tombstone=DbTombstoneMarker(pool),
            dry_run=dry_run,
        )
    finally:
        await pool.close()
    return affected


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(prog="cleanup_clients")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
