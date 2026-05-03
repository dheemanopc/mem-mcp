"""Identity management handlers: list, link start, unlink, promote."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel

from mem_mcp.db.tenant_tx import tenant_tx
from mem_mcp.identity.linking import LinkingError, start_link
from mem_mcp.identity.unlinking import (
    CognitoAdminDeleter,
    PromotionError,
    UnlinkingError,
    promote_primary,
    unlink_identity,
)
from mem_mcp.web.sessions import lookup_session

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


class LinkStartBody(BaseModel):
    """Request body (empty for now)."""

    pass


def make_identities_router(
    *,
    pool: asyncpg.Pool,
    audit: Any,  # AuditLogger Protocol
    admin_deleter: CognitoAdminDeleter,
    hmac_secret: str,
    cognito_authorize_base_url: str,
    cognito_client_id: str,
    callback_url: str,
) -> APIRouter:
    """Factory for identities router."""
    router = APIRouter()

    @router.get("/api/web/identities")
    async def list_identities(
        mem_session: str | None = Cookie(default=None),
    ) -> list[dict[str, Any]]:
        """List all identities for the tenant."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session")

        async with tenant_tx(pool, sess.tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT id, email, provider, is_primary, linked_at, last_seen_at
                FROM tenant_identities
                WHERE tenant_id = $1
                ORDER BY linked_at
                """,
                sess.tenant_id,
            )

        return [dict(r) for r in rows]

    @router.post("/api/web/identities/link/start")
    async def start_link_route(
        mem_session: str | None = Cookie(default=None),
    ) -> Response:
        """Initiate identity linking. Returns authorize URL + sets link_state_cookie."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session")

        try:
            result = await start_link(
                pool,
                tenant_id=sess.tenant_id,
                hmac_secret=hmac_secret,
                cognito_authorize_base_url=cognito_authorize_base_url,
                cognito_client_id=cognito_client_id,
                redirect_uri=callback_url,
            )
        except LinkingError as e:
            raise HTTPException(status_code=400, detail={"code": e.code}) from e

        response = Response(
            content=f'{{"authorize_url": "{result.authorize_url.replace('"', '\\"')}", "signed_state": "{result.signed_state}"}}',
            media_type="application/json",
        )
        response.set_cookie(
            "link_state_cookie",
            result.nonce,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=600,  # 10 min per LLD
        )
        return response

    @router.delete("/api/web/identities/{identity_id}")
    async def unlink_route(
        identity_id: UUID,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, bool]:
        """Unlink an identity."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session")

        try:
            await unlink_identity(
                pool,
                tenant_id=sess.tenant_id,
                identity_id=identity_id,
                deleter=admin_deleter,
                audit=audit,
                request_id=str(uuid4()),
            )
        except UnlinkingError as e:
            status = 400 if e.code in ("last_identity", "is_primary") else 404
            raise HTTPException(status, detail={"code": e.code}) from e

        return {"ok": True}

    @router.post("/api/web/identities/{identity_id}/promote")
    async def promote_route(
        identity_id: UUID,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, bool]:
        """Promote an identity to primary."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session")

        try:
            await promote_primary(
                pool,
                tenant_id=sess.tenant_id,
                identity_id=identity_id,
                audit=audit,
                request_id=str(uuid4()),
            )
        except PromotionError as e:
            raise HTTPException(status_code=400, detail={"code": e.code}) from e

        return {"ok": True}

    return router
