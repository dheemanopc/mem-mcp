"""memory.supersede tool — explicit a→b supersedence (T-7.5)."""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.memory.versioning import VERSIONED_TYPES


class MemorySupersedeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_id: UUID
    new_id: UUID


class MemorySupersedeOutput(BaseModel):
    old_id: UUID
    new_id: UUID
    request_id: str


class MemorySupersedeTool(BaseTool):
    """Explicit supersedence: mark old memory as superseded by new (T-7.5)."""

    name: ClassVar[str] = "memory.supersede"
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = MemorySupersedeInput
    OutputModel: ClassVar[type[BaseModel]] = MemorySupersedeOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemorySupersedeInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            # Lookup old
            old = await conn.fetchrow(
                """
                SELECT id, type, version, deleted_at
                FROM memories
                WHERE id = $1 AND tenant_id = $2
                """,
                inp.old_id,
                ctx.tenant_id,
            )
            if old is None:
                raise JsonRpcError(
                    -32602,
                    "old memory not found",
                    data={"errors": [{"path": "old_id", "message": "not found in tenant scope"}]},
                )
            if old["deleted_at"] is not None:
                raise JsonRpcError(
                    -32602,
                    "cannot supersede deleted memory",
                    data={"errors": [{"path": "old_id", "message": "memory is deleted"}]},
                )
            if old["type"] not in VERSIONED_TYPES:
                raise JsonRpcError(
                    -32602,
                    "supersede only valid for decision or fact",
                    data={
                        "errors": [
                            {
                                "path": "old_id",
                                "message": f"type {old['type']!r} is not versioned",
                            }
                        ]
                    },
                )

            # Lookup new
            new = await conn.fetchrow(
                """
                SELECT id, type, version, deleted_at
                FROM memories
                WHERE id = $1 AND tenant_id = $2
                """,
                inp.new_id,
                ctx.tenant_id,
            )
            if new is None:
                raise JsonRpcError(
                    -32602,
                    "new memory not found",
                    data={"errors": [{"path": "new_id", "message": "not found in tenant scope"}]},
                )
            if new["deleted_at"] is not None:
                raise JsonRpcError(
                    -32602,
                    "cannot supersede with deleted memory",
                    data={"errors": [{"path": "new_id", "message": "memory is deleted"}]},
                )
            if new["type"] != old["type"]:
                raise JsonRpcError(
                    -32602,
                    "type mismatch",
                    data={
                        "errors": [
                            {
                                "path": "new_id",
                                "message": f"type {new['type']!r} != {old['type']!r}",
                            }
                        ]
                    },
                )

            # UPDATE old: mark superseded
            await conn.execute(
                """
                UPDATE memories
                SET is_current = false, superseded_by = $1
                WHERE id = $2 AND tenant_id = $3
                """,
                inp.new_id,
                inp.old_id,
                ctx.tenant_id,
            )

            # UPDATE new: set version and mark as current
            new_version = int(old["version"]) + 1
            await conn.execute(
                """
                UPDATE memories
                SET supersedes = $1, version = $2, is_current = true
                WHERE id = $3 AND tenant_id = $4
                """,
                inp.old_id,
                new_version,
                inp.new_id,
                ctx.tenant_id,
            )

            # Audit
            await ctx.deps.audit.audit(
                conn,
                action="memory.supersede",
                result="success",
                tenant_id=ctx.tenant_id,
                identity_id=ctx.identity_id,
                client_id=ctx.client_id,
                target_id=inp.new_id,
                target_kind="memory",
                request_id=ctx.request_id,
                details={
                    "old_id": str(inp.old_id),
                    "new_id": str(inp.new_id),
                },
            )

        return MemorySupersedeOutput(
            old_id=inp.old_id,
            new_id=inp.new_id,
            request_id=ctx.request_id,
        )
