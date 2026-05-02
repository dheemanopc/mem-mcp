"""Audit logger — INSERT one row per audited event into audit_log.

Per spec §5.5:
  - Every mutation, every search, every auth event writes a row.
  - Append-only at the application layer (mem_app role has INSERT only;
    UPDATE/DELETE granted only to mem_maint).
  - Audit rows go on the SAME connection as the operation being audited
    (per LLD §4.9), so a failing tool rolls back its audit too. The one
    exception is auth-failure paths where there's no tenant tx — those
    callers open a fresh system_tx for the audit row.
  - The audit() function NEVER raises — failures emit structlog WARN
    + a counter metric (deferred for now). Audit losses are operationally
    visible but never break a request.

The full FR-5.5.5 action list is enumerated in AUDIT_ACTIONS so callers
get type-checking via Literal.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import UUID

from mem_mcp.logging_setup import get_logger

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


_log = get_logger("mem_mcp.audit")


# Per spec FR-5.5.5 — canonical action strings. Adding new ones here keeps
# call sites type-safe via Literal.
AUDIT_ACTIONS: tuple[str, ...] = (
    # auth
    "auth.token_issued",
    "auth.token_refresh",
    "auth.token_refresh_reuse",
    "auth.token_revoked",
    "auth.session_started",
    # oauth / DCR
    "oauth.dcr_register",
    "oauth.dcr_rejected",
    "oauth.client_revoked",
    # tenant lifecycle
    "tenant.created",
    "tenant.suspended",
    "tenant.deletion_requested",
    "tenant.deletion_cancelled",
    "tenant.deleted",
    # identity
    "identity.linked",
    "identity.unlinked",
    # memory tools
    "memory.write",
    "memory.search",
    "memory.get",
    "memory.list",
    "memory.update",
    "memory.delete",
    "memory.undelete",
    "memory.supersede",
    "memory.export",
    "memory.feedback",
    "memory.dedupe_merged",
    # quotas / abuse
    "quota.exceeded",
    "ratelimit.exceeded",
)


AuditAction = Literal[
    "auth.token_issued",
    "auth.token_refresh",
    "auth.token_refresh_reuse",
    "auth.token_revoked",
    "auth.session_started",
    "oauth.dcr_register",
    "oauth.dcr_rejected",
    "oauth.client_revoked",
    "tenant.created",
    "tenant.suspended",
    "tenant.deletion_requested",
    "tenant.deletion_cancelled",
    "tenant.deleted",
    "identity.linked",
    "identity.unlinked",
    "memory.write",
    "memory.search",
    "memory.get",
    "memory.list",
    "memory.update",
    "memory.delete",
    "memory.undelete",
    "memory.supersede",
    "memory.export",
    "memory.feedback",
    "memory.dedupe_merged",
    "quota.exceeded",
    "ratelimit.exceeded",
]

AuditResult = Literal["success", "denied", "error"]


class AuditLogger(Protocol):
    """Boundary for writing audit_log rows (test seam)."""

    async def audit(
        self,
        conn: asyncpg.Connection,
        *,
        action: AuditAction,
        result: AuditResult,
        tenant_id: UUID | None,
        identity_id: UUID | None = None,
        client_id: str | None = None,
        target_id: UUID | None = None,
        target_kind: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None: ...


class DbAuditLogger:
    """Production AuditLogger — INSERT INTO audit_log on the caller's connection.

    Rationale (LLD §4.9): the audit row goes on the SAME conn as the
    operation, so the parent transaction's rollback also rolls back the
    audit. Auth-failure paths that don't have a tenant tx open their
    own system_tx and pass that conn here.

    NEVER raises to caller. Failures emit structlog WARN.
    """

    async def audit(
        self,
        conn: asyncpg.Connection,
        *,
        action: AuditAction,
        result: AuditResult,
        tenant_id: UUID | None,
        identity_id: UUID | None = None,
        client_id: str | None = None,
        target_id: UUID | None = None,
        target_kind: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        try:
            await conn.execute(
                """
                INSERT INTO audit_log (
                    tenant_id, actor_client_id, actor_identity_id,
                    action, target_id, target_kind,
                    ip_address, user_agent, request_id,
                    result, error_code, details
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)
                """,
                tenant_id,
                client_id,
                identity_id,
                action,
                target_id,
                target_kind,
                ip_address,
                user_agent,
                request_id,
                result,
                error_code,
                json.dumps(details or {}),
            )
        except Exception as exc:
            _log.warning(
                "audit_insert_failed",
                action=action,
                result=result,
                tenant_id=str(tenant_id) if tenant_id else None,
                request_id=request_id,
                error=str(exc)[:300],
            )


class NoopAuditLogger:
    """Drops every audit call. For tests / dev / unit boots without the audit table."""

    async def audit(
        self,
        conn: asyncpg.Connection,
        *,
        action: AuditAction,
        result: AuditResult,
        tenant_id: UUID | None,
        identity_id: UUID | None = None,
        client_id: str | None = None,
        target_id: UUID | None = None,
        target_kind: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        pass
