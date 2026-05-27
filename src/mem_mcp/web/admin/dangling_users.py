"""Admin endpoints for reviewing and triggering Cognito user reconciliation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from mem_mcp.audit.logger import AuditLogger
from mem_mcp.auth.permissions import Permission
from mem_mcp.auth.rbac import require_permission
from mem_mcp.jobs.reconcile_signups import (
    BotoCognitoUserLister,
    ReconcileReport,
    _classify,
    reconcile_signups,
)

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

# Pre-built FastAPI dependency; computed once at import time
_DEP_MANAGE_TENANTS = Depends(require_permission(Permission.SYSTEM_MANAGE_TENANTS))


class ProvisionableUser(BaseModel):
    """A Cognito user ready to be provisioned."""

    email: str
    sub: str
    workspace_domain: str | None = None


class DanglingUsersResponse(BaseModel):
    """Response from GET /api/web/admin/dangling-users."""

    provisionable: list[ProvisionableUser]
    dangling_uninvited: list[str] = Field(default_factory=list)
    orphan_identities: list[str] = Field(default_factory=list)
    skipped_existing: int = 0


def make_dangling_users_router(
    *,
    pool: asyncpg.Pool,
    cognito_lister: BotoCognitoUserLister,
    audit: AuditLogger,
) -> APIRouter:
    """Factory for admin dangling-users router."""
    router = APIRouter()

    @router.get("/api/web/admin/dangling-users")
    async def list_dangling(
        caller_tenant_id: UUID = _DEP_MANAGE_TENANTS,
    ) -> DanglingUsersResponse:
        """Classify Cognito users into provisionable, dangling, and orphan buckets (read-only)."""
        # Fetch Cognito users
        cognito_users = await cognito_lister.list_users()

        # Classify
        to_provision, dangling_uninvited, orphan_identities, skipped_count = await _classify(
            pool, cognito_users
        )

        return DanglingUsersResponse(
            provisionable=[
                ProvisionableUser(
                    email=u.email,
                    sub=u.sub,
                    workspace_domain=u.workspace_domain,
                )
                for u in to_provision
            ],
            dangling_uninvited=dangling_uninvited,
            orphan_identities=orphan_identities,
            skipped_existing=skipped_count,
        )

    @router.post("/api/web/admin/dangling-users/reconcile")
    async def trigger_reconcile(
        caller_tenant_id: UUID = _DEP_MANAGE_TENANTS,
    ) -> dict[str, Any]:
        """Trigger immediate reconciliation (mutates DB)."""
        report: ReconcileReport = await reconcile_signups(
            pool,
            cognito_lister,
            audit,
            dry_run=False,
        )

        return {
            "provisioned": report.provisioned,
            "provisioned_count": len(report.provisioned),
            "skipped_existing": report.skipped_existing,
            "dangling_uninvited": report.dangling_uninvited,
            "orphan_identities": report.orphan_identities,
        }

    return router
