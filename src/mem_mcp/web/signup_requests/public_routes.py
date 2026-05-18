"""Public (no-auth) routes for self-serve signup requests."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from mem_mcp.audit.logger import AuditLogger
from mem_mcp.config import get_settings
from mem_mcp.db import system_tx
from mem_mcp.logging_setup import get_logger
from mem_mcp.web.signup_requests import emails, rate_limit, repository

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

_log = get_logger("mem_mcp.signup_requests.public")


class SubmitInput(BaseModel):
    email: EmailStr
    name: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=1000)
    client_intent: list[str] = Field(default_factory=list)
    # Honeypot: must be empty or absent. Silently drops if filled.
    website: str | None = Field(default=None)


def make_public_signup_router(*, pool: asyncpg.Pool, audit: AuditLogger) -> APIRouter:
    router = APIRouter()

    @router.post("/api/public/signup-requests", status_code=202)
    async def submit(payload: SubmitInput, request: Request) -> dict[str, str]:
        # Honeypot trap
        if payload.website:
            _log.info("signup_request_honeypot", email=payload.email)
            return {"status": "submitted"}

        # Rate limits: 10/IP/day, 3/email/day
        ip = request.client.host if request.client else None
        if ip and not await rate_limit.check_and_increment(
            pool, key=f"signup:ip:{ip}", bucket_seconds=86400, limit=10
        ):
            raise HTTPException(status_code=429, detail="too many submissions from this network")
        if not await rate_limit.check_and_increment(
            pool, key=f"signup:email:{payload.email.lower()}", bucket_seconds=86400, limit=3
        ):
            raise HTTPException(status_code=429, detail="too many submissions for this email")

        # Tag normalisation
        client_intent = [s.strip().lower() for s in payload.client_intent if s.strip()]
        client_intent = client_intent[:6]  # cap

        try:
            req, raw_token = await repository.upsert_request(
                pool,
                email=payload.email.lower(),
                name=payload.name,
                reason=payload.reason,
                client_intent=client_intent,
                ip_address=ip,
                user_agent=request.headers.get("user-agent"),
            )
        except ValueError:
            # Already approved/rejected — return generic 202 to avoid leaking
            return {"status": "submitted"}

        verify_url = f"https://memapp.dheemantech.in/auth/verify-email?token={raw_token}"
        await emails.send_verification_email(to=payload.email, verify_url=verify_url)

        # Audit (no tenant — system action)
        async with system_tx(pool) as conn:
            await audit.audit(
                conn,
                action="signup.requested",
                result="success",
                tenant_id=None,
                identity_id=None,
                client_id="public",
                target_id=req.id,
                target_kind="signup_request",
                request_id=str(req.id),
                details={"email_domain": payload.email.split("@")[-1]},
            )

        return {"status": "submitted"}

    @router.get("/api/public/signup-requests/verify")
    async def verify(token: str) -> dict[str, str]:
        if not re.match(r"^[A-Za-z0-9_-]{20,128}$", token):
            raise HTTPException(status_code=400, detail="invalid token format")
        req = await repository.verify_token(pool, raw_token=token)
        if req is None:
            raise HTTPException(status_code=400, detail="token expired or invalid")

        s = get_settings()
        await emails.send_operator_notification(
            operator_email=s.operator_email,
            applicant_email=req.email,
            applicant_name=req.name,
        )

        async with system_tx(pool) as conn:
            await audit.audit(
                conn,
                action="signup.verified",
                result="success",
                tenant_id=None,
                identity_id=None,
                client_id="public",
                target_id=req.id,
                target_kind="signup_request",
                request_id=str(req.id),
                details={},
            )

        return {"status": "verified", "email": req.email}

    return router
