"""POST /api/admin/kite-intents — S2S endpoint for partner apps (e.g.,
algotrade) to push Kite credentials as an encrypted short-lived "intent".
The user then confirms via /api/web/skills/kite/confirm before the creds land in
their tenant vault.

Auth: Bearer JWT (Cognito client_credentials grant) with scope `admin.kite_intent`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from mem_mcp.audit.logger import DbAuditLogger
from mem_mcp.auth.jwt_validator import JwtValidator
from mem_mcp.config import Settings
from mem_mcp.db import system_tx
from mem_mcp.skills.vault import SkillVault

INTENT_TTL_SECONDS = 600  # 10 minutes


class _KiteCredsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(min_length=1)
    api_secret: str = Field(min_length=1)
    user_id: str | None = None
    password: str | None = None
    totp_secret: str | None = None
    orders_enabled: bool = False


class CreateKiteIntentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    kite: _KiteCredsBody


class CreateKiteIntentResponse(BaseModel):
    intent_id: UUID
    confirm_url: str
    expires_at: datetime


def make_kite_intent_router(
    *,
    pool: asyncpg.Pool,
    audit: DbAuditLogger,
    vault: SkillVault,
    jwt_validator: JwtValidator,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin"])

    async def _require_s2s_scope(request: Request) -> dict[str, Any]:
        """Validate Bearer JWT and require admin.kite_intent scope.
        Returns the verified claims."""
        auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
        if not auth_header or not auth_header.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing Bearer token",
                headers={"WWW-Authenticate": 'Bearer realm="mem-mcp"'},
            )
        token = auth_header[len("Bearer ") :].strip()
        try:
            claims = await jwt_validator.validate(token)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"invalid token: {exc}",
                headers={"WWW-Authenticate": 'Bearer realm="mem-mcp", error="invalid_token"'},
            ) from exc
        scope_claim = claims.raw.get("scope") or claims.raw.get("scp") or ""
        if isinstance(scope_claim, list):
            scopes = set(scope_claim)
        else:
            scopes = set(str(scope_claim).split())
        if "admin.kite_intent" not in scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="missing required scope admin.kite_intent",
            )
        return claims.raw

    @router.post(
        "/kite-intents",
        response_model=CreateKiteIntentResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_intent(
        body: CreateKiteIntentBody,
        request: Request,
        claims: dict[str, Any] = Depends(_require_s2s_scope),  # noqa: B008
    ) -> CreateKiteIntentResponse:
        kite = body.kite
        # Validate Mode C trio is all-or-nothing
        partial = any(
            x is not None for x in (kite.user_id, kite.password, kite.totp_secret)
        ) and not all(x for x in (kite.user_id, kite.password, kite.totp_secret))
        if partial:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="auto-login requires user_id, password, and totp_secret together (or none)",
            )

        # Lookup tenant by email (system tx — no RLS narrowing yet)
        async with system_tx(pool) as conn:
            row = await conn.fetchrow(
                "SELECT id FROM tenants WHERE email = $1",
                str(body.email),
            )
            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="tenant_not_found",
                )
            tenant_id: UUID = row["id"]

        # Encrypt creds payload using vault helper
        creds_dict = kite.model_dump()
        plaintext = json.dumps(creds_dict).encode("utf-8")
        ciphertext, encryption_metadata = vault.encrypt_intent(
            plaintext=plaintext,
            tenant_id=tenant_id,
            aad_label="kite_intent",
        )

        intent_id = uuid4()
        expires_at = datetime.now(UTC) + timedelta(seconds=INTENT_TTL_SECONDS)
        client_id = str(claims.get("client_id") or claims.get("sub") or "unknown")

        # Insert under RLS
        async with system_tx(pool) as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            await conn.execute(
                """
                INSERT INTO kite_intents
                  (id, tenant_id, ciphertext, encryption_metadata, created_by_client_id, expires_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6)
                """,
                intent_id,
                tenant_id,
                ciphertext,
                json.dumps(encryption_metadata),
                client_id,
                expires_at,
            )
            await audit.audit(
                conn,
                action="skill.kite.intent_created",
                result="success",
                tenant_id=tenant_id,
                identity_id=None,
                client_id=client_id,
                target_id=intent_id,
                target_kind="kite_intent",
                request_id=request.headers.get("x-request-id", str(uuid4())),
                details={
                    "has_auto_login": bool(kite.user_id),
                    "orders_enabled": kite.orders_enabled,
                },
            )

        confirm_url = f"{settings.web_url.rstrip('/')}/skills/kite/confirm?intent={intent_id}"
        return CreateKiteIntentResponse(
            intent_id=intent_id,
            confirm_url=confirm_url,
            expires_at=expires_at,
        )

    return router
