"""memory.delete tool — soft-delete current version (recoverable for 30d)."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.memory.versioning import VERSIONED_TYPES


class MemoryDeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    cascade: bool = False  # if True, delete entire versioned chain (requires memory.admin)


class MemoryDeleteOutput(BaseModel):
    id: UUID
    deleted_at: datetime
    promoted_version_id: UUID | None  # if a prior version was made current, this is its id
    cascaded_count: int = 0  # number of additional rows soft-deleted via cascade
    request_id: str


class MemoryDeleteTool(BaseTool):
    """Soft-delete a memory. For versioned types, promotes the most recent
    prior version to is_current=true unless cascade=true."""

    name: ClassVar[str] = "memory.delete"
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = MemoryDeleteInput
    OutputModel: ClassVar[type[BaseModel]] = MemoryDeleteOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemoryDeleteInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        # cascade requires memory.admin scope (per FR-9.3.6.2)
        if inp.cascade and "memory.admin" not in ctx.scopes:
            raise JsonRpcError(
                -32000,
                "cascade requires memory.admin scope",
                data={
                    "code": "insufficient_scope",
                    "required_scopes": ["memory.admin"],
                    "granted_scopes": sorted(ctx.scopes),
                },
            )

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            # Look up the target row to find supersedes/type
            target = await conn.fetchrow(
                """
                SELECT id, type, supersedes, is_current, deleted_at
                FROM memories
                WHERE id = $1 AND tenant_id = $2
                """,
                inp.id,
                ctx.tenant_id,
            )
            if target is None:
                raise JsonRpcError(
                    -32602,
                    "memory not found",
                    data={"errors": [{"path": "id", "message": "not found in tenant scope"}]},
                )
            if target["deleted_at"] is not None:
                raise JsonRpcError(
                    -32602,
                    "memory already deleted",
                    data={"errors": [{"path": "id", "message": "deleted_at is already set"}]},
                )

            promoted_version_id: UUID | None = None
            cascaded_count = 0

            if inp.cascade:
                # Soft-delete every row in the chain (walk supersedes chain backwards from target)
                rows = await conn.fetch(
                    """
                    WITH RECURSIVE chain AS (
                        SELECT id, supersedes FROM memories WHERE id = $1 AND tenant_id = $2
                        UNION ALL
                        SELECT m.id, m.supersedes FROM memories m
                        JOIN chain c ON m.id = c.supersedes
                        WHERE m.tenant_id = $2 AND m.deleted_at IS NULL
                    )
                    UPDATE memories
                    SET deleted_at = now(), is_current = false
                    WHERE id IN (SELECT id FROM chain)
                      AND tenant_id = $2
                      AND deleted_at IS NULL
                    RETURNING id
                    """,
                    inp.id,
                    ctx.tenant_id,
                )
                cascaded_count = max(len(rows) - 1, 0)
                deleted_at = await conn.fetchval(
                    "SELECT deleted_at FROM memories WHERE id = $1 AND tenant_id = $2",
                    inp.id,
                    ctx.tenant_id,
                )
            else:
                # Soft-delete only this row
                deleted_at = await conn.fetchval(
                    """
                    UPDATE memories SET deleted_at = now(), is_current = false
                    WHERE id = $1 AND tenant_id = $2
                    RETURNING deleted_at
                    """,
                    inp.id,
                    ctx.tenant_id,
                )

                # FR-10.6.4: promote prior version if this was current AND a prior live version exists
                if (
                    target["is_current"]
                    and target["type"] in VERSIONED_TYPES
                    and target["supersedes"] is not None
                ):
                    prior = await conn.fetchrow(
                        """
                        SELECT id FROM memories
                        WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL
                        """,
                        target["supersedes"],
                        ctx.tenant_id,
                    )
                    if prior is not None:
                        promoted_version_id = prior["id"]
                        await conn.execute(
                            "UPDATE memories SET is_current = true WHERE id = $1 AND tenant_id = $2",
                            promoted_version_id,
                            ctx.tenant_id,
                        )

            await ctx.deps.audit.audit(
                conn,
                action="memory.delete",
                result="success",
                tenant_id=ctx.tenant_id,
                identity_id=ctx.identity_id,
                client_id=ctx.client_id,
                target_id=inp.id,
                target_kind="memory",
                request_id=ctx.request_id,
                details={
                    "cascade": inp.cascade,
                    "cascaded_count": cascaded_count,
                    "promoted_version_id": str(promoted_version_id)
                    if promoted_version_id
                    else None,
                },
            )

        return MemoryDeleteOutput(
            id=inp.id,
            deleted_at=deleted_at,
            promoted_version_id=promoted_version_id,
            cascaded_count=cascaded_count,
            request_id=ctx.request_id,
        )
