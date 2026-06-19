"""mindmap_link — draw a typed edge between two memories.

Thin wrapper over the core memory_references graph using the map edge
vocabulary (displaced-from, resolves-under, dropped-under, superseded-under,
open-under, principle-under, ...). Resolution + access checks mirror the
memory_write reference path.
"""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, Field

from mem_mcp.db import system_tx, tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.mcp.tools._mindmap_common import resolve_team_id
from mem_mcp.mindmap import service
from mem_mcp.teams.references import (
    ReferenceTargetNotFoundError,
    insert_reference,
    resolve_reference_target,
)


class MindmapLinkInput(BaseModel):
    source_memory_id: UUID
    target_memory_id: UUID
    reference_kind: str = Field(..., min_length=1, max_length=128)
    map_key: str | None = Field(
        default=None, description="If given, the link is logged as a map event."
    )
    team_id: UUID | None = None


class MindmapLinkOutput(BaseModel):
    source_memory_id: UUID
    target_memory_id: UUID
    reference_kind: str
    request_id: str


class MindmapLinkTool(BaseTool):
    name: ClassVar[str] = "mindmap_link"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["mindmap_link"]
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = MindmapLinkInput
    OutputModel: ClassVar[type[BaseModel]] = MindmapLinkOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MindmapLinkInput)

        async with system_tx(ctx.db_pool) as sys_conn:
            try:
                resolved = await resolve_reference_target(
                    sys_conn,
                    target_uuid=inp.target_memory_id,
                    caller_user_id=ctx.tenant_id,
                )
            except ReferenceTargetNotFoundError as exc:
                raise JsonRpcError(
                    -32602,
                    "reference target not found or not accessible",
                    data={"code": "memory_not_accessible"},
                ) from exc

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            await insert_reference(
                conn,
                source_memory_id=inp.source_memory_id,
                target_memory_id=resolved["memory_id"],
                target_team_id=resolved["team_id"],
                reference_kind=inp.reference_kind,
            )
            if inp.map_key is not None:
                team_id = await resolve_team_id(conn, ctx, inp.team_id)
                root_id = await service.resolve_map_root(
                    conn, team_id=team_id, map_key=inp.map_key
                )
                if root_id is not None:
                    await service.log_event(
                        conn,
                        root_memory_id=root_id,
                        tenant_id=ctx.tenant_id,
                        event="link",
                        actor="model",
                        memory_id=inp.source_memory_id,
                        payload={
                            "target": str(resolved["memory_id"]),
                            "reference_kind": inp.reference_kind,
                        },
                    )

        return MindmapLinkOutput(
            source_memory_id=inp.source_memory_id,
            target_memory_id=resolved["memory_id"],
            reference_kind=inp.reference_kind,
            request_id=ctx.request_id,
        )
