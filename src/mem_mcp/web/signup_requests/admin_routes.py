"""Admin routes for signup-request review. Permission-gated via RBAC."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mem_mcp.audit.logger import AuditLogger
from mem_mcp.auth.permissions import Permission
from mem_mcp.auth.rbac import require_permission
from mem_mcp.db import get_pool, system_tx
from mem_mcp.invites import seed_invite_email
from mem_mcp.web.signup_requests import emails, repository

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


# Pre-built FastAPI dependencies; computed once at import time so ruff's
# B008 (function call in argument default) doesn't fire.
_DEP_REVIEW_SIGNUPS = Depends(require_permission(Permission.SYSTEM_REVIEW_SIGNUPS))
_DEP_APPROVE_SIGNUPS = Depends(require_permission(Permission.SYSTEM_APPROVE_SIGNUPS))
_DEP_REJECT_SIGNUPS = Depends(require_permission(Permission.SYSTEM_REJECT_SIGNUPS))


class ApproveInput(BaseModel):
    notes: str | None = Field(default=None, max_length=1000)


class RejectInput(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)
    notes: str | None = Field(default=None, max_length=1000)
    send_email: bool = False


async def _get_reviewer_email(tenant_id: UUID) -> str:
    """Fetch the email for a tenant_id from tenant_identities."""
    pool = get_pool()
    async with system_tx(pool) as conn:
        row = await conn.fetchrow(
            "SELECT email FROM tenant_identities WHERE id IN (SELECT identity_id FROM tenants WHERE id = $1)",
            tenant_id,
        )
    if row is None or row["email"] is None:
        return f"tenant:{tenant_id}"  # fallback if identity email not found
    return str(row["email"])


def make_admin_signup_router(*, pool: asyncpg.Pool, audit: AuditLogger) -> APIRouter:
    router = APIRouter()

    @router.get("/api/web/admin/signup-requests")
    async def list_all(
        status: str | None = None,
        caller_tenant_id: UUID = _DEP_REVIEW_SIGNUPS,
    ) -> dict[str, Any]:
        if status and status not in (
            "awaiting_verification",
            "pending",
            "approved",
            "rejected",
            "expired",
        ):
            raise HTTPException(status_code=400, detail="bad status filter")
        rows = await repository.list_requests(pool, status_filter=status, limit=100)
        return {
            "requests": [
                {
                    "id": str(r.id),
                    "email": r.email,
                    "name": r.name,
                    "reason": r.reason,
                    "client_intent": r.client_intent,
                    "status": r.status,
                    "source": r.source,
                    "submitted_at": r.submitted_at.isoformat(),
                    "email_verified_at": r.email_verified_at.isoformat()
                    if r.email_verified_at
                    else None,
                    "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
                    "reviewed_by": r.reviewed_by,
                }
                for r in rows
            ],
        }

    @router.post("/api/web/admin/signup-requests/{request_id}/approve")
    async def approve(
        request_id: UUID,
        payload: ApproveInput,
        caller_tenant_id: UUID = _DEP_APPROVE_SIGNUPS,
    ) -> dict[str, Any]:
        reviewer_email = await _get_reviewer_email(caller_tenant_id)
        req = await repository.mark_reviewed(
            pool,
            request_id=request_id,
            status="approved",
            reviewer=reviewer_email,
            notes=payload.notes,
        )
        if req is None:
            raise HTTPException(status_code=409, detail="not in pending state or not found")
        # Insert into invited_emails
        await seed_invite_email(
            pool,
            email=req.email,
            invited_by=reviewer_email,
            notes=f"approved via signup_requests {req.id}",
        )
        await emails.send_approval_email(to=req.email)
        async with system_tx(pool) as conn:
            await audit.audit(
                conn,
                action="signup.approved",
                result="success",
                tenant_id=None,
                identity_id=None,
                client_id="web-admin",
                target_id=req.id,
                target_kind="signup_request",
                request_id=str(req.id),
                details={
                    "reviewer_tenant_id": str(caller_tenant_id),
                    "reviewer_email": reviewer_email,
                    "applicant_email": req.email,
                },
            )
        return {"status": "approved", "id": str(req.id)}

    @router.post("/api/web/admin/signup-requests/{request_id}/reject")
    async def reject(
        request_id: UUID,
        payload: RejectInput,
        caller_tenant_id: UUID = _DEP_REJECT_SIGNUPS,
    ) -> dict[str, Any]:
        reviewer_email = await _get_reviewer_email(caller_tenant_id)
        req = await repository.mark_reviewed(
            pool,
            request_id=request_id,
            status="rejected",
            reviewer=reviewer_email,
            notes=f"{payload.reason}\n\n{payload.notes or ''}".strip(),
        )
        if req is None:
            raise HTTPException(status_code=409, detail="not in pending state or not found")
        if payload.send_email:
            await emails.send_rejection_email(to=req.email, notes=payload.notes)
        async with system_tx(pool) as conn:
            await audit.audit(
                conn,
                action="signup.rejected",
                result="success",
                tenant_id=None,
                identity_id=None,
                client_id="web-admin",
                target_id=req.id,
                target_kind="signup_request",
                request_id=str(req.id),
                details={
                    "reviewer_tenant_id": str(caller_tenant_id),
                    "reviewer_email": reviewer_email,
                    "reason": payload.reason,
                    "email_sent": payload.send_email,
                },
            )
        return {"status": "rejected", "id": str(req.id)}

    return router
