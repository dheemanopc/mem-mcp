"""Admin routes for signup-request review. Email-match guard against settings.operator_email."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel, Field

from mem_mcp.audit.logger import AuditLogger
from mem_mcp.config import get_settings
from mem_mcp.db import system_tx
from mem_mcp.invites import seed_invite_email
from mem_mcp.web.sessions import lookup_session
from mem_mcp.web.signup_requests import emails, repository

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


class ApproveInput(BaseModel):
    notes: str | None = Field(default=None, max_length=1000)


class RejectInput(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)
    notes: str | None = Field(default=None, max_length=1000)
    send_email: bool = False


async def _require_operator(pool: asyncpg.Pool, mem_session: str | None) -> str:
    """Return operator email if session belongs to operator. Else 401/403."""
    if not mem_session:
        raise HTTPException(status_code=401, detail="not authenticated")
    sess = await lookup_session(pool, mem_session)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid session")
    # Pull email from tenant_identities (system context since looking up by identity_id)
    async with system_tx(pool) as conn:
        row = await conn.fetchrow(
            "SELECT email FROM tenant_identities WHERE id = $1",
            sess.identity_id,
        )
    if row is None or row["email"] is None:
        raise HTTPException(status_code=403, detail="email not found on identity")
    operator_email = get_settings().operator_email.lower()
    if row["email"].lower() != operator_email:
        raise HTTPException(status_code=403, detail="not an operator")
    return str(row["email"])


def make_admin_signup_router(*, pool: asyncpg.Pool, audit: AuditLogger) -> APIRouter:
    router = APIRouter()

    @router.get("/api/web/admin/signup-requests")
    async def list_all(
        status: str | None = None,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        await _require_operator(pool, mem_session)
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
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        reviewer = await _require_operator(pool, mem_session)
        req = await repository.mark_reviewed(
            pool,
            request_id=request_id,
            status="approved",
            reviewer=reviewer,
            notes=payload.notes,
        )
        if req is None:
            raise HTTPException(status_code=409, detail="not in pending state or not found")
        # Insert into invited_emails
        await seed_invite_email(
            pool,
            email=req.email,
            invited_by=reviewer,
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
                details={"reviewer": reviewer, "applicant_email": req.email},
            )
        return {"status": "approved", "id": str(req.id)}

    @router.post("/api/web/admin/signup-requests/{request_id}/reject")
    async def reject(
        request_id: UUID,
        payload: RejectInput,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        reviewer = await _require_operator(pool, mem_session)
        req = await repository.mark_reviewed(
            pool,
            request_id=request_id,
            status="rejected",
            reviewer=reviewer,
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
                    "reviewer": reviewer,
                    "reason": payload.reason,
                    "email_sent": payload.send_email,
                },
            )
        return {"status": "rejected", "id": str(req.id)}

    return router
