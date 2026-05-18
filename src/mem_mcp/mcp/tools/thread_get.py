"""memory.thread_get tool — fetch a root memory + all its replies in chronological order."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext


class MemoryThreadGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_id: UUID


class ThreadMemoryRecord(BaseModel):
    id: UUID
    content: str
    type: str
    tags: list[str]
    metadata: dict[str, Any]
    version: int
    is_current: bool
    parent_id: UUID | None
    supersedes: UUID | None
    superseded_by: UUID | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    @field_validator("metadata", mode="before")
    @classmethod
    def _parse_jsonb(cls, v: Any) -> Any:
        # See get.py MemoryRecord._parse_jsonb — same asyncpg codec quirk.
        if isinstance(v, str):
            return json.loads(v) if v else {}
        return v


class MemoryThreadGetOutput(BaseModel):
    root: ThreadMemoryRecord
    replies: list[ThreadMemoryRecord]
    request_id: str


class MemoryThreadGetTool(BaseTool):
    """Fetch a root memory + all its replies in chronological order."""

    name: ClassVar[str] = "memory_thread_get"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memory_thread_get"]
    required_scope: ClassVar[str] = "memory.read"
    InputModel: ClassVar[type[BaseModel]] = MemoryThreadGetInput
    OutputModel: ClassVar[type[BaseModel]] = MemoryThreadGetOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemoryThreadGetInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            root = await conn.fetchrow(
                """
                SELECT id, content, type, tags, metadata, version, is_current,
                       parent_id, supersedes, superseded_by,
                       created_at, updated_at, deleted_at
                FROM memories
                WHERE id = $1 AND tenant_id = $2
                """,
                inp.root_id,
                ctx.tenant_id,
            )
            if root is None:
                raise JsonRpcError(
                    -32602,
                    "root memory not found",
                    data={
                        "errors": [
                            {
                                "path": "root_id",
                                "message": "not found in tenant scope",
                            }
                        ]
                    },
                )

            if root["deleted_at"] is not None:
                raise JsonRpcError(
                    -32602,
                    "root memory is soft-deleted",
                    data={
                        "errors": [
                            {
                                "path": "root_id",
                                "message": "cannot fetch thread of a deleted memory",
                            }
                        ]
                    },
                )

            if root["parent_id"] is not None:
                raise JsonRpcError(
                    -32602,
                    "root_id refers to a reply, not a root",
                    data={
                        "errors": [
                            {
                                "path": "root_id",
                                "message": "memory_thread_get requires a root memory id (parent_id IS NULL)",
                            }
                        ]
                    },
                )

            replies_rows = await conn.fetch(
                """
                SELECT id, content, type, tags, metadata, version, is_current,
                       parent_id, supersedes, superseded_by,
                       created_at, updated_at, deleted_at
                FROM memories
                WHERE parent_id = $1 AND tenant_id = $2 AND deleted_at IS NULL
                ORDER BY created_at ASC
                """,
                inp.root_id,
                ctx.tenant_id,
            )

            await ctx.deps.audit.audit(
                conn,
                action="memory.thread_get",
                result="success",
                tenant_id=ctx.tenant_id,
                identity_id=ctx.identity_id,
                client_id=ctx.client_id,
                target_id=inp.root_id,
                target_kind="memory",
                request_id=ctx.request_id,
                details={"reply_count": len(replies_rows)},
            )

        return MemoryThreadGetOutput(
            root=ThreadMemoryRecord(**dict(root)),
            replies=[ThreadMemoryRecord(**dict(r)) for r in replies_rows],
            request_id=ctx.request_id,
        )
