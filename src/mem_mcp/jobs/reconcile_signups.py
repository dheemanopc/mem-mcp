"""Periodic job to reconcile Cognito users with tenant provisioning.

Sweeps Cognito ListUsers for users that signed up but lack a tenant_identities row,
INSERTs missing tenants/tenant_identities, auto-consumes pending team_invites with
matching workspace_domain (mirroring /auth/callback logic).

Runs via systemd timer every 5 minutes. Can also be invoked manually:
  python -m mem_mcp.jobs reconcile_signups [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from mem_mcp.audit.logger import AuditLogger, DbAuditLogger
from mem_mcp.config import get_settings
from mem_mcp.db.tenant_tx import system_tx
from mem_mcp.identity.personal_team import ensure_personal_team
from mem_mcp.logging_setup import get_logger, setup_logging

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

_log = get_logger("mem_mcp.jobs.reconcile_signups")


@dataclass(frozen=True)
class CognitoUserSummary:
    """A Cognito user snapshot from ListUsers."""

    sub: str
    username: str
    email: str
    email_verified: bool
    workspace_domain: str | None
    enabled: bool
    user_status: str
    identities_json: str | None


@dataclass
class ReconcileReport:
    """Results from a reconciliation run."""

    provisioned: list[str] = field(default_factory=list)
    skipped_existing: int = 0
    dangling_uninvited: list[str] = field(default_factory=list)
    orphan_identities: list[str] = field(default_factory=list)


class CognitoUserLister(Protocol):
    """Protocol for listing Cognito users."""

    async def list_users(self) -> Sequence[CognitoUserSummary]: ...


class BotoCognitoUserLister:
    """Lists Cognito users via boto3 cognito-idp.list_users."""

    def __init__(self, user_pool_id: str, region: str = "ap-south-1") -> None:
        import boto3  # type: ignore[import-untyped]

        self._client: Any = boto3.client("cognito-idp", region_name=region)
        self._user_pool_id = user_pool_id

    async def list_users(self) -> Sequence[CognitoUserSummary]:
        """Paginate Cognito users, max 60 per call, return all as CognitoUserSummary."""

        def _list_users_sync() -> list[CognitoUserSummary]:
            """Synchronous version of list_users for asyncio.to_thread."""
            users: list[CognitoUserSummary] = []
            paginator = self._client.get_paginator("list_users")
            for page in paginator.paginate(
                UserPoolId=self._user_pool_id, PaginationConfig={"PageSize": 60}
            ):
                for user in page.get("Users", []):
                    # Parse Attributes (list of {Name, Value} dicts)
                    attrs: dict[str, str] = {}
                    for attr in user.get("Attributes", []):
                        attrs[attr["Name"]] = attr["Value"]

                    # Extract fields, with careful parsing for booleans
                    email_verified_str = attrs.get("email_verified", "false")
                    email_verified = email_verified_str.lower() == "true"

                    users.append(
                        CognitoUserSummary(
                            sub=attrs.get("sub", ""),
                            username=attrs.get("cognito:username", attrs.get("sub", "")),
                            email=attrs.get("email", ""),
                            email_verified=email_verified,
                            workspace_domain=attrs.get("custom:google_hd"),
                            enabled=bool(user.get("Enabled", False)),
                            user_status=str(user.get("UserStatus", "")),
                            identities_json=attrs.get("identities"),
                        )
                    )
            return users

        result: list[CognitoUserSummary] = await asyncio.to_thread(_list_users_sync)
        return result


def _derive_provider(identities_json: str | None) -> tuple[str, str | None]:
    """Extract provider name and user ID from Cognito identities JSON.

    Args:
        identities_json: JSON-encoded list from Cognito's identities attribute.

    Returns:
        (provider_name, provider_user_id) tuple. Default ("cognito", None) on error.
    """
    if not identities_json:
        return ("cognito", None)

    try:
        identities: Any = json.loads(identities_json)

        if not identities:
            return ("cognito", None)

        first: dict[str, Any] = identities[0]
        provider_name = first.get("providerName", "cognito").lower()
        user_id = first.get("userId")
        return (provider_name, user_id)
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return ("cognito", None)


async def _consume_pending_invites(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    email: str,
    workspace_domain: str | None,
    audit: AuditLogger,
) -> int:
    """Accept pending team invites matching email and domain rule.

    Mirrors routes.py:222-275 exactly. Returns count accepted.
    """
    count_accepted = 0

    # Fetch pending invites by email
    pending_invites = await conn.fetch(
        """
        SELECT team_id, role, invited_by_tenant_id FROM team_invites
        WHERE LOWER(email) = LOWER($1) AND status = 'pending'
        """,
        email,
    )

    # For each pending invite, validate domain rule and accept if applicable
    for invite in pending_invites:
        team = await conn.fetchrow(
            "SELECT workspace_domain FROM teams WHERE id = $1 AND deleted_at IS NULL",
            invite["team_id"],
        )
        if not team:
            continue  # Team was deleted; skip this invite

        team_workspace_domain = team["workspace_domain"]

        # Validate domain rules
        domain_match = False
        if team_workspace_domain:
            # Workspace team: caller must share domain
            domain_match = workspace_domain == team_workspace_domain
        else:
            # Personal team: caller must have no domain
            domain_match = workspace_domain is None

        if domain_match:
            # Accept the invite
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO team_members (team_id, tenant_id, role, status, added_by_tenant_id)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT DO NOTHING
                    """,
                    invite["team_id"],
                    tenant_id,
                    invite["role"],
                    "active",
                    invite["invited_by_tenant_id"],
                )
                await conn.execute(
                    """
                    UPDATE team_invites
                    SET status = 'accepted'
                    WHERE team_id = $1 AND LOWER(email) = LOWER($2)
                    """,
                    invite["team_id"],
                    email,
                )
                count_accepted += 1

    return count_accepted


async def _classify(
    pool: asyncpg.Pool,
    cognito_users: Sequence[CognitoUserSummary],
) -> tuple[list[CognitoUserSummary], list[str], list[str], int]:
    """Classify Cognito users into three buckets: to_provision, dangling, orphan.

    Returns:
        (to_provision, dangling_uninvited_emails, orphan_identity_emails, skipped_count)
    """
    # One system_tx to fetch existing identities and invites
    async with system_tx(pool) as conn:
        # Fetch all existing (sub, email) from tenant_identities
        existing_identities = await conn.fetch(
            "SELECT cognito_sub, email FROM tenant_identities WHERE cognito_sub IS NOT NULL"
        )
        existing_subs = {row["cognito_sub"] for row in existing_identities}
        existing_identity_emails = {
            row["email"].lower() if row["email"] else None for row in existing_identities
        }

        # Fetch all pending invite emails
        invited_emails = await conn.fetch("SELECT email FROM invited_emails")
        invited_emails_set = {row["email"].lower() for row in invited_emails}

    # Classify Cognito users
    to_provision = []
    dangling_uninvited = []
    orphan_identities = []
    skipped_count = 0

    # Compute cognito_emails from full Cognito set (before filtering)
    cognito_emails = {u.email.lower() for u in cognito_users if u.email}

    for user in cognito_users:
        # Skip users that don't meet basic criteria
        if not (user.enabled and user.email_verified and user.email):
            skipped_count += 1
            continue
        # Check if sub already in tenant_identities; if so, skip entirely
        if user.sub in existing_subs:
            skipped_count += 1
            continue  # already a tenant; no further classification
        # User is new and meets criteria: provision it
        if user.email.lower() in invited_emails_set:
            to_provision.append(user)
        else:
            dangling_uninvited.append(user.email)

    # Orphan identities: emails in DB that aren't in Cognito
    for email in existing_identity_emails:
        if email and email not in cognito_emails:
            orphan_identities.append(email)

    return to_provision, dangling_uninvited, orphan_identities, skipped_count


async def _provision_one(
    pool: asyncpg.Pool,
    user: CognitoUserSummary,
    audit: AuditLogger,
) -> tuple[bool, str | None]:
    """Provision a single Cognito user as a tenant.

    Returns:
        (success, error_message)
    """
    try:
        async with system_tx(pool) as conn:
            # INSERT tenant
            tenant_row = await conn.fetchrow(
                """
                INSERT INTO tenants (email, display_name)
                VALUES ($1, $2)
                RETURNING id
                """,
                user.email,
                user.username,
            )
            if not tenant_row:
                return False, "Failed to insert tenant"

            tenant_id = tenant_row["id"]

            # Derive provider from identities
            provider, provider_user_id = _derive_provider(user.identities_json)

            # INSERT tenant_identities
            await conn.execute(
                """
                INSERT INTO tenant_identities
                (tenant_id, cognito_sub, cognito_username, provider, provider_user_id, email, is_primary)
                VALUES ($1, $2, $3, $4, $5, $6, TRUE)
                """,
                tenant_id,
                user.sub,
                user.username,
                provider,
                provider_user_id,
                user.email,
            )

            # If user has workspace domain, update tenant_identities
            if user.workspace_domain:
                await conn.execute(
                    """
                    UPDATE tenant_identities
                    SET workspace_domain = $1
                    WHERE tenant_id = $2
                    """,
                    user.workspace_domain,
                    tenant_id,
                )

            # Personal team + owner role + default_team_id. Without this the
            # tenant exists but MCP is unusable for them: no team resolves and
            # they hold no role. Omitted here until 2026-08-11, so every tenant
            # this job provisioned was born unable to log in.
            await ensure_personal_team(conn, tenant_id, user.email)

            # Consume pending invites
            count_accepted = await _consume_pending_invites(
                conn,
                tenant_id=tenant_id,
                email=user.email,
                workspace_domain=user.workspace_domain,
                audit=audit,
            )

            # Audit
            await audit.audit(
                conn,
                action="tenant.provisioned_by_reconcile",
                result="success",
                tenant_id=tenant_id,
                details={
                    "email": user.email,
                    "source": "reconcile_signups",
                    "invites_accepted": count_accepted,
                },
            )

        return True, None
    except Exception as exc:
        _log.warning(
            "signup_reconciled_provision_failed",
            email=user.email,
            error=str(exc),
        )
        return False, str(exc)


async def reconcile_signups(
    pool: asyncpg.Pool,
    cognito_lister: CognitoUserLister,
    audit: AuditLogger,
    *,
    dry_run: bool = False,
) -> ReconcileReport:
    """Orchestrate user reconciliation from Cognito to tenant provisioning.

    Args:
        pool: asyncpg pool
        cognito_lister: Cognito user lister (BotoCognitoUserLister in prod)
        audit: Audit logger
        dry_run: If True, log what would be provisioned but don't mutate DB

    Returns:
        ReconcileReport with provisioned, skipped, dangling, and orphan counts
    """
    _log.info("reconcile_signups_started", dry_run=dry_run)

    # Fetch all Cognito users
    cognito_users = await cognito_lister.list_users()

    # Classify into buckets
    to_provision, dangling_uninvited, orphan_identities, skipped_count = await _classify(
        pool, cognito_users
    )

    report = ReconcileReport(
        skipped_existing=skipped_count,
        dangling_uninvited=dangling_uninvited,
        orphan_identities=orphan_identities,
    )

    if dry_run:
        # Dry run: just populate report with would-be-provisioned emails
        report.provisioned = [u.email for u in to_provision]
        _log.info(
            "reconcile_signups_done",
            provisioned_count=len(report.provisioned),
            dangling_count=len(dangling_uninvited),
            orphan_count=len(orphan_identities),
            skipped_count=skipped_count,
            dry_run=True,
        )
        return report

    # Real run: provision each user
    for user in to_provision:
        ok, err = await _provision_one(pool, user, audit)
        if ok:
            _log.info("signup_reconciled_provisioned", email=user.email)
            report.provisioned.append(user.email)
        else:
            _log.warning("signup_reconciled_provision_failed", email=user.email, error=err)

    # Log dangling and orphan buckets
    for email in dangling_uninvited:
        _log.info("signup_reconciled_dangling_uninvited", email=email)

    for email in orphan_identities:
        _log.info("signup_reconciled_orphan_identity", email=email)

    _log.info(
        "reconcile_signups_done",
        provisioned_count=len(report.provisioned),
        dangling_count=len(dangling_uninvited),
        orphan_count=len(orphan_identities),
        skipped_count=skipped_count,
        dry_run=False,
    )

    return report


async def main(dry_run: bool = False) -> int:
    """Main entrypoint for the reconcile job."""
    setup_logging(get_settings().log_level)
    settings = get_settings()

    import asyncpg

    pool: Any = await asyncpg.create_pool(
        dsn=settings.db_maint_dsn_asyncpg,
        min_size=1,
        max_size=2,
        command_timeout=60,
        server_settings={"application_name": "mem-mcp-reconcile-signups"},
    )
    try:
        cognito_lister = BotoCognitoUserLister(
            user_pool_id=settings.cognito_user_pool_id,
            region=settings.region,
        )
        audit = DbAuditLogger()

        report = await reconcile_signups(pool, cognito_lister, audit, dry_run=dry_run)
        return len(report.provisioned)
    finally:
        await pool.close()


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(prog="reconcile_signups")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
