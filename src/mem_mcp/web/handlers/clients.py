"""OAuth client management handlers: list, revoke."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi import APIRouter, Cookie, HTTPException

from mem_mcp.db.tenant_tx import system_tx
from mem_mcp.oauth.revoke import CognitoClientDeleter, RevokeClientError, revoke_client
from mem_mcp.web.sessions import lookup_session

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


def make_clients_router(
    *,
    pool: asyncpg.Pool,
    audit: Any,  # AuditLogger Protocol
    client_deleter: CognitoClientDeleter,
) -> APIRouter:
    """Factory for clients router."""
    router = APIRouter()

    @router.get("/api/web/clients")
    async def list_clients(
        mem_session: str | None = Cookie(default=None),
    ) -> list[dict[str, Any]]:
        """List OAuth clients for the tenant."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session")

        async with system_tx(pool) as conn:
            rows = await conn.fetch(
                """
                SELECT id, client_name, software_id, review_status, disabled, created_at, last_used_at
                FROM oauth_clients
                WHERE tenant_id = $1 AND deleted_at IS NULL
                ORDER BY created_at DESC
                """,
                sess.tenant_id,
            )

        return [dict(r) for r in rows]

    @router.delete("/api/web/clients/{client_id}")
    async def revoke_route(
        client_id: str,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, bool]:
        """Revoke an OAuth client."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session")

        try:
            await revoke_client(
                pool,
                tenant_id=sess.tenant_id,
                client_id=client_id,
                deleter=client_deleter,
                audit=audit,
                request_id=str(uuid4()),
            )
        except RevokeClientError as e:
            status = 404 if e.code == "not_found" else 400
            raise HTTPException(status, detail={"code": e.code}) from e

        return {"ok": True}

    return router
