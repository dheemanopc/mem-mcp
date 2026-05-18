"""Web routes for Kite credential intent confirmation.

GET /api/web/skills/kite/intent/{intent_id} — fetch intent summary (no secrets)
POST /api/web/skills/kite/intent/{intent_id}/confirm — accept and store credentials
POST /api/web/skills/kite/intent/{intent_id}/cancel — reject intent
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, status
from pydantic import BaseModel

from mem_mcp.audit.logger import DbAuditLogger
from mem_mcp.db import system_tx
from mem_mcp.skills.kite.onboarding import KiteOnboardingError, enable_kite_for_tenant
from mem_mcp.skills.vault import SkillVault, SkillVaultError
from mem_mcp.web.sessions import lookup_session


class IntentSummaryResponse(BaseModel):
    intent_id: UUID
    api_key_last4: str
    user_id: str | None
    has_auto_login: bool
    orders_enabled: bool
    expires_at: datetime
    created_by_client_id: str


class ConfirmResponse(BaseModel):
    status: str
    mode: str | None = None
    kite_user_id: str | None = None


class CancelResponse(BaseModel):
    status: str


def make_kite_confirm_router(
    *,
    pool: asyncpg.Pool,
    audit: DbAuditLogger,
    vault: SkillVault,
) -> APIRouter:
    router = APIRouter(prefix="/api/web/skills/kite", tags=["skills"])

    async def _require_session(
        mem_session: str | None = Cookie(default=None),
    ) -> tuple[UUID, UUID]:
        """Validate session cookie and return (tenant_id, identity_id)."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session")
        return sess.tenant_id, sess.identity_id

    @router.get(
        "/intent/{intent_id}",
        response_model=IntentSummaryResponse,
        status_code=status.HTTP_200_OK,
    )
    async def get_intent(
        intent_id: UUID,
        tenant_data: tuple[UUID, UUID] = Depends(_require_session),
    ) -> IntentSummaryResponse:
        tenant_id, _ = tenant_data
        async with system_tx(pool) as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            row = await conn.fetchrow(
                """
                SELECT id, ciphertext, encryption_metadata, created_by_client_id, expires_at, consumed_at
                FROM kite_intents
                WHERE id = $1 AND tenant_id = $2
                """,
                intent_id,
                tenant_id,
            )

        if row is None:
            raise HTTPException(status_code=404, detail="intent_not_found")

        if row["consumed_at"] is not None:
            raise HTTPException(status_code=410, detail="intent_already_consumed")

        if row["expires_at"] < datetime.now(datetime.now().astimezone().tzinfo):
            raise HTTPException(status_code=410, detail="intent_expired")

        # Decrypt to extract metadata
        try:
            ciphertext = bytes(row["ciphertext"])
            plaintext = vault.decrypt_intent(
                blob=ciphertext,
                tenant_id=tenant_id,
                aad_label="kite_intent",
            )
            creds = json.loads(plaintext.decode("utf-8"))
        except SkillVaultError as exc:
            raise HTTPException(status_code=500, detail=f"decryption failed: {exc}") from exc

        api_key = creds.get("api_key", "")
        api_key_last4 = f"...{api_key[-4:]}" if len(api_key) >= 4 else "..."
        user_id = creds.get("user_id")
        has_auto_login = bool(user_id and creds.get("password") and creds.get("totp_secret"))

        return IntentSummaryResponse(
            intent_id=intent_id,
            api_key_last4=api_key_last4,
            user_id=user_id,
            has_auto_login=has_auto_login,
            orders_enabled=creds.get("orders_enabled", False),
            expires_at=row["expires_at"],
            created_by_client_id=row["created_by_client_id"],
        )

    @router.post(
        "/intent/{intent_id}/confirm",
        response_model=ConfirmResponse,
        status_code=status.HTTP_200_OK,
    )
    async def confirm_intent(
        intent_id: UUID,
        request: Request,
        tenant_data: tuple[UUID, UUID] = Depends(_require_session),
    ) -> ConfirmResponse:
        tenant_id, identity_id = tenant_data
        async with system_tx(pool) as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            row = await conn.fetchrow(
                """
                SELECT ciphertext, encryption_metadata, created_by_client_id, expires_at, consumed_at
                FROM kite_intents
                WHERE id = $1 AND tenant_id = $2
                """,
                intent_id,
                tenant_id,
            )

            if row is None:
                raise HTTPException(status_code=404, detail="intent_not_found")

            if row["consumed_at"] is not None:
                raise HTTPException(status_code=410, detail="intent_already_consumed")

            if row["expires_at"] < datetime.now(datetime.now().astimezone().tzinfo):
                await conn.execute(
                    """
                    UPDATE kite_intents
                    SET consumed_at = now(), consumed_outcome = $1
                    WHERE id = $2
                    """,
                    "expired",
                    intent_id,
                )
                raise HTTPException(status_code=410, detail="intent_expired")

            # Decrypt creds
            try:
                ciphertext = bytes(row["ciphertext"])
                plaintext = vault.decrypt_intent(
                    blob=ciphertext,
                    tenant_id=tenant_id,
                    aad_label="kite_intent",
                )
                creds = json.loads(plaintext.decode("utf-8"))
            except SkillVaultError as exc:
                await conn.execute(
                    """
                    UPDATE kite_intents
                    SET consumed_at = now(), consumed_outcome = $1
                    WHERE id = $2
                    """,
                    "decrypt_failed",
                    intent_id,
                )
                raise HTTPException(status_code=500, detail=f"decryption failed: {exc}") from exc

            # Run smoke test + store in vault
            try:
                result = await enable_kite_for_tenant(
                    conn=conn,
                    tenant_id=tenant_id,
                    creds=creds,
                    vault=vault,
                    run_smoke_test=True,
                )
            except KiteOnboardingError as exc:
                await conn.execute(
                    """
                    UPDATE kite_intents
                    SET consumed_at = now(), consumed_outcome = $1
                    WHERE id = $2
                    """,
                    "smoke_failed",
                    intent_id,
                )
                await audit.audit(
                    conn,
                    action="skill.kite.intent_smoke_failed",
                    result="error",
                    tenant_id=tenant_id,
                    identity_id=identity_id,
                    client_id="web",
                    target_id=intent_id,
                    target_kind="kite_intent",
                    request_id=request.headers.get("x-request-id", str(intent_id)),
                    details={"error": str(exc)},
                )
                raise HTTPException(status_code=400, detail=f"smoke test failed: {exc}") from exc

            # Mark intent as consumed
            await conn.execute(
                """
                UPDATE kite_intents
                SET consumed_at = now(), consumed_outcome = $1
                WHERE id = $2
                """,
                "confirmed",
                intent_id,
            )

            await audit.audit(
                conn,
                action="skill.kite.intent_confirmed",
                result="success",
                tenant_id=tenant_id,
                identity_id=identity_id,
                client_id="web",
                target_id=intent_id,
                target_kind="kite_intent",
                request_id=request.headers.get("x-request-id", str(intent_id)),
                details={
                    "mode": result.mode,
                    "kite_user_id": result.kite_user_id,
                },
            )

        return ConfirmResponse(status="success", mode=result.mode, kite_user_id=result.kite_user_id)

    @router.post(
        "/intent/{intent_id}/cancel",
        response_model=CancelResponse,
        status_code=status.HTTP_200_OK,
    )
    async def cancel_intent(
        intent_id: UUID,
        request: Request,
        tenant_data: tuple[UUID, UUID] = Depends(_require_session),
    ) -> CancelResponse:
        tenant_id, identity_id = tenant_data
        async with system_tx(pool) as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            await conn.execute(
                """
                UPDATE kite_intents
                SET consumed_at = now(), consumed_outcome = $1
                WHERE id = $2 AND tenant_id = $3 AND consumed_at IS NULL
                """,
                "cancelled",
                intent_id,
                tenant_id,
            )

            await audit.audit(
                conn,
                action="skill.kite.intent_cancelled",
                result="success",
                tenant_id=tenant_id,
                identity_id=identity_id,
                client_id="web",
                target_id=intent_id,
                target_kind="kite_intent",
                request_id=request.headers.get("x-request-id", str(intent_id)),
            )

        return CancelResponse(status="cancelled")

    return router
