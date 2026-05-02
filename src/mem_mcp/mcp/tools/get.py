"""memory.get tool — fetch one memory + optional history."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import BaseTool, ToolContext


class MemoryGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    include_history: bool = False


class MemoryRecord(BaseModel):
    id: UUID
    content: str
    type: str
    tags: list[str]
    metadata: dict[str, Any]
    version: int
    is_current: bool
    supersedes: UUID | None
    superseded_by: UUID | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class MemoryGetOutput(BaseModel):
    memory: MemoryRecord
    history: list[MemoryRecord]


class MemoryGetTool(BaseTool):
    """Fetch one memory by id (current version + history if requested)."""

    name: ClassVar[str] = "memory.get"
    required_scope: ClassVar[str] = "memory.read"
    InputModel: ClassVar[type[BaseModel]] = MemoryGetInput
    OutputModel: ClassVar[type[BaseModel]] = MemoryGetOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemoryGetInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT id, content, type, tags, metadata, version, is_current,
                       supersedes, superseded_by, created_at, updated_at, deleted_at
                FROM memories
                WHERE id = $1 AND tenant_id = $2
                """,
                inp.id,
                ctx.tenant_id,
            )
            if row is None:
                raise JsonRpcError(
                    -32602,
                    "memory not found",
                    data={
                        "errors": [
                            {
                                "path": "id",
                                "message": "not found in tenant scope",
                            }
                        ]
                    },
                )

            history: list[dict[str, Any]] = []
            if inp.include_history:
                # Walk supersedes chain backwards
                hrows = await conn.fetch(
                    """
                    WITH RECURSIVE chain AS (
                        SELECT id, content, type, tags, metadata, version, is_current,
                               supersedes, superseded_by, created_at, updated_at, deleted_at
                        FROM memories
                        WHERE id = $1 AND tenant_id = $2
                        UNION ALL
                        SELECT m.id, m.content, m.type, m.tags, m.metadata, m.version,
                               m.is_current, m.supersedes, m.superseded_by,
                               m.created_at, m.updated_at, m.deleted_at
                        FROM memories m JOIN chain c ON m.id = c.supersedes
                        WHERE m.tenant_id = $2
                    )
                    SELECT * FROM chain WHERE id <> $1
                    ORDER BY version DESC
                    """,
                    inp.id,
                    ctx.tenant_id,
                )
                history = [dict(h) for h in hrows]

            await ctx.deps.audit.audit(
                conn,
                action="memory.get",
                result="success",
                tenant_id=ctx.tenant_id,
                identity_id=ctx.identity_id,
                client_id=ctx.client_id,
                target_id=inp.id,
                target_kind="memory",
                request_id=ctx.request_id,
                details={
                    "include_history": inp.include_history,
                    "history_count": len(history),
                },
            )

        return MemoryGetOutput(
            memory=MemoryRecord(**dict(row)),
            history=[MemoryRecord(**h) for h in history],
        )
