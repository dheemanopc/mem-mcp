"""memory.export tool — DPDP right-to-access full data dump (T-7.6)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import BaseTool, ToolContext


class MemoryExportInput(BaseModel):
    """Input for memory.export tool."""

    model_config = ConfigDict(extra="forbid")


class MemoryExportOutput(BaseModel):
    """Output for memory.export tool."""

    exported_at: datetime
    memories: list[dict[str, Any]]
    audit_log: list[dict[str, Any]]
    request_id: str


class MemoryExportTool(BaseTool):
    """Export full JSON dump of all memories + audit_log for tenant (DPDP right to access)."""

    name: ClassVar[str] = "memory.export"
    required_scope: ClassVar[str] = "memory.admin"
    InputModel: ClassVar[type[BaseModel]] = MemoryExportInput
    OutputModel: ClassVar[type[BaseModel]] = MemoryExportOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemoryExportInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        # Scope check: memory.admin required (FR-9.3.9.2)
        if "memory.admin" not in ctx.scopes:
            raise JsonRpcError(
                -32000,
                "memory.export requires memory.admin scope",
                data={
                    "code": "insufficient_scope",
                    "required_scopes": ["memory.admin"],
                    "granted_scopes": sorted(ctx.scopes),
                },
            )

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            # Fetch all memories (current + history + soft-deleted) for tenant
            memory_rows = await conn.fetch(
                """
                SELECT id, type, content, tags, metadata, version,
                       supersedes, superseded_by, is_current,
                       created_at, updated_at, deleted_at
                FROM memories
                WHERE tenant_id = $1
                ORDER BY created_at
                """,
                ctx.tenant_id,
            )

            # Fetch all audit_log rows for tenant
            audit_rows = await conn.fetch(
                """
                SELECT id, action, result, target_id, target_kind,
                       request_id, details, created_at
                FROM audit_log
                WHERE tenant_id = $1
                ORDER BY created_at
                """,
                ctx.tenant_id,
            )

            # Convert Records to dicts
            memories_list = [dict(row) for row in memory_rows]
            audit_list = [dict(row) for row in audit_rows]

            # Audit the export
            await ctx.deps.audit.audit(
                conn,
                action="memory.export",
                result="success",
                tenant_id=ctx.tenant_id,
                identity_id=ctx.identity_id,
                client_id=ctx.client_id,
                target_id=None,
                target_kind=None,
                request_id=ctx.request_id,
                details={
                    "memories_count": len(memories_list),
                    "audit_count": len(audit_list),
                },
            )

        return MemoryExportOutput(
            exported_at=datetime.now(tz=UTC),
            memories=memories_list,
            audit_log=audit_list,
            request_id=ctx.request_id,
        )
