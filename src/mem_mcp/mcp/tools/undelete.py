"""memory.undelete tool — restore a soft-deleted memory within 30-day grace."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.memory.versioning import VERSIONED_TYPES

UNDELETE_GRACE_DAYS = 30


class MemoryUndeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID


class MemoryUndeleteOutput(BaseModel):
    id: UUID
    deleted_at: datetime | None  # null after undelete
    is_current: bool
    request_id: str


class MemoryUndeleteTool(BaseTool):
    """Restore a soft-deleted memory if within 30-day grace window.

    Per FR-9.3.7.2: if the memory is part of a versioned chain and another
    row in the chain is currently is_current=true, refuse the undelete to
    avoid two siblings being current simultaneously.
    """

    name: ClassVar[str] = "memory.undelete"
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = MemoryUndeleteInput
    OutputModel: ClassVar[type[BaseModel]] = MemoryUndeleteOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemoryUndeleteInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            target = await conn.fetchrow(
                """
                SELECT id, type, deleted_at, supersedes, superseded_by, is_current,
                       (now() - deleted_at) AS age
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
            if target["deleted_at"] is None:
                raise JsonRpcError(
                    -32602,
                    "memory not deleted",
                    data={
                        "errors": [
                            {"path": "id", "message": "deleted_at is null; nothing to undelete"}
                        ]
                    },
                )

            # Grace check (FR-9.3.7.1)
            if target["age"] > timedelta(days=UNDELETE_GRACE_DAYS):
                raise JsonRpcError(
                    -32000,
                    "cannot undelete past grace period",
                    data={
                        "code": "cannot_undelete_after_grace_period",
                        "grace_days": UNDELETE_GRACE_DAYS,
                    },
                )

            # FR-9.3.7.2: for versioned chains, ensure no current sibling
            should_be_current = True
            if target["type"] in VERSIONED_TYPES:
                # Search for any sibling in the chain that's currently is_current=true
                conflicting = await conn.fetchval(
                    """
                    SELECT count(*) FROM memories
                    WHERE tenant_id = $1
                      AND deleted_at IS NULL
                      AND is_current = true
                      AND id != $2
                      AND (
                        id = $3                 -- the row we superseded (our supersedes)
                        OR id = $4              -- the row that superseded us (our superseded_by)
                        OR supersedes = $2      -- a row that supersedes us
                        OR superseded_by = $2   -- a row that we superseded (back-pointer)
                      )
                    """,
                    ctx.tenant_id,
                    inp.id,
                    target["supersedes"],
                    target["superseded_by"],
                )
                if conflicting and int(conflicting) > 0:
                    should_be_current = False  # restore but don't make current

            new_is_current = should_be_current
            row = await conn.fetchrow(
                """
                UPDATE memories
                SET deleted_at = NULL, is_current = $3
                WHERE id = $1 AND tenant_id = $2
                RETURNING deleted_at, is_current
                """,
                inp.id,
                ctx.tenant_id,
                new_is_current,
            )

            await ctx.deps.audit.audit(
                conn,
                action="memory.undelete",
                result="success",
                tenant_id=ctx.tenant_id,
                identity_id=ctx.identity_id,
                client_id=ctx.client_id,
                target_id=inp.id,
                target_kind="memory",
                request_id=ctx.request_id,
                details={"is_current_after": new_is_current},
            )

        return MemoryUndeleteOutput(
            id=inp.id,
            deleted_at=row["deleted_at"],
            is_current=row["is_current"],
            request_id=ctx.request_id,
        )
