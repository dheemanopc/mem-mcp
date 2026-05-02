"""Internal invite-allowlist check endpoint.

Called by the PreSignUp Lambda (T-4.8) during Cognito sign-up to verify the
email is on the invited_emails allowlist. HMAC-shared-secret authenticated
via X-Internal-Auth header.

v1 simplified per LLD §0: only checks invited_emails (no tenant creation, no
link-mode awareness, no email-collision branch — Google-only IdP makes the
collision case structurally impossible).
"""

from __future__ import annotations

import hmac
from hashlib import sha256
from typing import TYPE_CHECKING, Literal, Protocol

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from starlette.responses import JSONResponse

from mem_mcp.db import system_tx
from mem_mcp.logging_setup import get_logger

if TYPE_CHECKING:
    import asyncpg


_log = get_logger("mem_mcp.auth.internal_invite")


# --------------------------------------------------------------------------
# Pydantic models
# --------------------------------------------------------------------------


class InviteCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    # Optional context the Lambda may pass; we don't use it in v1 but accept it
    provider: str | None = Field(default=None, max_length=32)


InviteDecision = Literal["allow", "deny"]
InviteReason = Literal["invited", "not_invited", "already_consumed"]


class InviteCheckResponse(BaseModel):
    decision: InviteDecision
    reason: InviteReason


# --------------------------------------------------------------------------
# Protocol (test seam)
# --------------------------------------------------------------------------


class InviteStore(Protocol):
    async def lookup(self, email: str) -> Literal["invited", "not_invited", "already_consumed"]:
        """Check the invited_emails table for the given (lowercased) email."""
        ...


class DbInviteStore:
    """Production store using asyncpg + system_tx."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def lookup(self, email: str) -> Literal["invited", "not_invited", "already_consumed"]:
        async with system_tx(self._pool) as conn:
            row = await conn.fetchrow(
                "SELECT consumed_at FROM invited_emails WHERE email = $1",
                email.lower(),
            )
        if row is None:
            return "not_invited"
        if row["consumed_at"] is not None:
            return "already_consumed"
        return "invited"


# --------------------------------------------------------------------------
# HMAC verification
# --------------------------------------------------------------------------


def _compute_hmac(secret: str, body: bytes) -> str:
    """Return the hex HMAC-SHA256 of body using secret."""
    return hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()


def _verify_hmac(presented: str, expected: str) -> bool:
    """Constant-time comparison."""
    return hmac.compare_digest(presented, expected)


# --------------------------------------------------------------------------
# Router factory
# --------------------------------------------------------------------------


def make_internal_invite_router(
    *,
    store: InviteStore,
    shared_secret: str,
) -> APIRouter:
    """Build the /internal/check_invite router.

    The shared_secret comes from Settings.internal_lambda_secret (loaded from
    SSM SecureString /mem-mcp/internal/lambda_secret at process startup).
    """
    router = APIRouter(tags=["internal"])

    @router.post("/internal/check_invite")
    async def check_invite(request: Request) -> JSONResponse:
        # Read raw body for HMAC verification BEFORE Pydantic parsing
        body_bytes = await request.body()

        # HMAC check
        presented = request.headers.get("x-internal-auth", "")
        if not presented:
            raise HTTPException(
                status_code=401,
                detail={"error": "unauthorized", "reason": "missing_internal_auth"},
            )

        expected = _compute_hmac(shared_secret, body_bytes)
        if not _verify_hmac(presented, expected):
            _log.warning(
                "internal_invite_hmac_mismatch",
                ip=request.client.host if request.client else "unknown",
            )
            raise HTTPException(
                status_code=401,
                detail={"error": "unauthorized", "reason": "hmac_mismatch"},
            )

        # Parse body
        try:
            payload_dict = (await request.json()) if body_bytes else {}
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_request", "reason": "non_json_body"},
            ) from None

        try:
            payload = InviteCheckRequest.model_validate(payload_dict)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_request", "reason": str(exc)[:300]},
            ) from exc

        # Look up
        status_value = await store.lookup(payload.email)

        # Map to allow/deny
        if status_value == "invited":
            response = InviteCheckResponse(decision="allow", reason="invited")
        elif status_value == "already_consumed":
            response = InviteCheckResponse(decision="deny", reason="already_consumed")
        else:
            response = InviteCheckResponse(decision="deny", reason="not_invited")

        # Log decision (email is hashed in case logs leak); audit log is T-5.12
        _log.info(
            "invite_check",
            email_hash=sha256(payload.email.lower().encode("utf-8")).hexdigest()[:12],
            decision=response.decision,
            reason=response.reason,
        )

        return JSONResponse(content=response.model_dump(), status_code=status.HTTP_200_OK)

    return router
