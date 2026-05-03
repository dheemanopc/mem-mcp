"""memory.feedback tool — beta feedback channel (T-7.8)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import BaseTool, ToolContext


class MemoryFeedbackInput(BaseModel):
    """Input for memory.feedback tool."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=4096)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryFeedbackOutput(BaseModel):
    """Output for memory.feedback tool."""

    id: UUID
    received_at: datetime
    request_id: str


class MemoryFeedbackTool(BaseTool):
    """Store beta feedback; non-blocking (FR-9.3.11.1)."""

    name: ClassVar[str] = "memory.feedback"
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = MemoryFeedbackInput
    OutputModel: ClassVar[type[BaseModel]] = MemoryFeedbackOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemoryFeedbackInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            # INSERT INTO feedback (tenant_id, client_id, text, metadata)
            # VALUES ($1, $2, $3, $4::jsonb) RETURNING id, created_at
            row = await conn.fetchrow(
                """
                INSERT INTO feedback (tenant_id, client_id, text, metadata)
                VALUES ($1, $2, $3, $4::jsonb)
                RETURNING id, created_at
                """,
                ctx.tenant_id,
                ctx.client_id,
                inp.text,
                json.dumps(inp.metadata),
            )

            if row is None:
                raise JsonRpcError(
                    -32603,
                    "failed to insert feedback",
                )

            # Audit the feedback
            await ctx.deps.audit.audit(
                conn,
                action="memory.feedback",
                result="success",
                tenant_id=ctx.tenant_id,
                identity_id=ctx.identity_id,
                client_id=ctx.client_id,
                target_id=row["id"],
                target_kind="feedback",
                request_id=ctx.request_id,
                details={"text_length": len(inp.text)},
            )

        return MemoryFeedbackOutput(
            id=row["id"],
            received_at=row["created_at"],
            request_id=ctx.request_id,
        )
