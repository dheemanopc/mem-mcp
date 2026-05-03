"""Tenant settings handlers: GET /api/web/me, PATCH /api/web/tenant, data deletion flow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi import APIRouter, Body, Cookie, HTTPException
from pydantic import BaseModel, Field

from mem_mcp.db.tenant_tx import system_tx
from mem_mcp.identity.lifecycle import (
    ClosureError,
    CognitoGlobalSignOutter,
    cancel_closure,
    request_closure,
)
from mem_mcp.web.sessions import lookup_session

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


class TenantPatchBody(BaseModel):
    """Request body for PATCH /api/web/tenant."""

    retention_days: int = Field(..., ge=7, le=3650)


class CancelDeleteBody(BaseModel):
    """Request body for POST /api/web/data/delete/cancel."""

    cancel_token: str


def make_tenant_router(
    *,
    pool: asyncpg.Pool,
    audit: Any,  # AuditLogger Protocol
    sign_outter: CognitoGlobalSignOutter,
) -> APIRouter:
    """Factory for tenant settings router."""
    router = APIRouter()

    @router.get("/api/web/me")
    async def get_me(
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        """Return {tenant_id, identity_id, tenant: {email, status, tier, retention_days}}."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session")

        async with system_tx(pool) as conn:
            tenant_row = await conn.fetchrow(
                "SELECT email, status, tier, retention_days FROM tenants WHERE id = $1",
                sess.tenant_id,
            )

        if tenant_row is None:
            raise HTTPException(status_code=404, detail="tenant not found")

        return {
            "tenant_id": str(sess.tenant_id),
            "identity_id": str(sess.identity_id),
            "tenant": dict(tenant_row),
        }

    @router.patch("/api/web/tenant")
    async def patch_tenant(
        body: TenantPatchBody = Body(...),  # noqa: B008
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, bool]:
        """Update retention_days. Email is read-only in v1."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session")

        async with system_tx(pool) as conn:
            await conn.execute(
                "UPDATE tenants SET retention_days = $1 WHERE id = $2",
                body.retention_days,
                sess.tenant_id,
            )

        return {"ok": True}

    @router.post("/api/web/data/delete")
    async def request_delete(
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        """Initiate account closure. Returns {cancel_until, identities_signed_out}.

        Cancel token is sent via email (out of scope).
        """
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session")

        try:
            result = await request_closure(
                pool,
                tenant_id=sess.tenant_id,
                sign_outter=sign_outter,
                audit=audit,
                request_id=str(uuid4()),
            )
        except ClosureError as e:
            raise HTTPException(
                status_code=400,
                detail={"code": e.code, "detail": e.detail or ""},
            ) from e

        return {
            "cancel_until": result.cancel_until.isoformat(),
            "identities_signed_out": result.identities_signed_out,
        }

    @router.post("/api/web/data/delete/cancel")
    async def cancel_delete(
        body: CancelDeleteBody = Body(...),  # noqa: B008
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, bool]:
        """Cancel a pending account closure."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session")

        try:
            await cancel_closure(
                pool,
                tenant_id=sess.tenant_id,
                cancel_token=body.cancel_token,
                audit=audit,
                request_id=str(uuid4()),
            )
        except ClosureError as e:
            raise HTTPException(
                status_code=400,
                detail={"code": e.code, "detail": e.detail or ""},
            ) from e

        return {"ok": True}

    return router
