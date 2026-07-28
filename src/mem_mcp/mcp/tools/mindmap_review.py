"""mindmap_review — run the structural reviewer pass and reset the counter.

The reviewer is a smoke-detector, not a judge (spec 6af0f365): it looks at the
map ONLY (no transcript — the threat model is mistakes, not cheating). The tool
itself makes no judgment; it returns the map's structural surface (nodes,
edges, recent events, pending flags) for the model / review sub-agent to
inspect, and resets writes_since_review so the next cycle starts clean.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, Field

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.mcp.tools._mindmap_common import resolve_team_id
from mem_mcp.mindmap import service


class MindmapReviewInput(BaseModel):
    map_key: str = Field(..., min_length=1, max_length=64)
    team_id: UUID | None = None


class MindmapReviewNode(BaseModel):
    memory_id: UUID
    node_role: str
    type: str
    content: str
    metadata: dict[str, Any]


class MindmapReviewOutput(BaseModel):
    map_key: str
    root_memory_id: UUID
    title: str
    state: str
    nodes: list[MindmapReviewNode]
    recent_events: list[dict[str, Any]]
    counter_reset_from: int
    request_id: str


class MindmapReviewTool(BaseTool):
    name: ClassVar[str] = "mindmap_review"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["mindmap_review"]
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = MindmapReviewInput
    OutputModel: ClassVar[type[BaseModel]] = MindmapReviewOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MindmapReviewInput)

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            team_id = await resolve_team_id(conn, ctx, inp.team_id)
            root_id = await service.resolve_map_root(conn, team_id=team_id, map_key=inp.map_key)
            if root_id is None:
                raise JsonRpcError(-32602, "map not found", data={"errors": [{"path": "map_key"}]})
            row = await service.get_map_row(conn, root_memory_id=root_id)
            assert row is not None
            members = await service.fetch_members(
                conn, root_memory_id=root_id, tenant_id=ctx.tenant_id
            )
            events = await service.fetch_recent_events(conn, root_memory_id=root_id)
            counter_was = int(row["writes_since_review"])
            await service.reset_review_counter(conn, root_memory_id=root_id)
            await service.log_event(
                conn,
                root_memory_id=root_id,
                tenant_id=ctx.tenant_id,
                event="review",
                actor="reviewer",
                payload={"counter_reset_from": counter_was},
            )

        nodes = [
            MindmapReviewNode(
                memory_id=m["memory_id"],
                node_role=m["node_role"],
                type=m["type"],
                content=m["content"],
                metadata=m["metadata"],
            )
            for m in members
        ]
        recent = [
            {
                "event": e["event"],
                "actor": e["actor"],
                "memory_id": str(e["memory_id"]) if e["memory_id"] else None,
                "payload": e["payload"],
                "created_at": e["created_at"].isoformat()
                if isinstance(e["created_at"], datetime)
                else str(e["created_at"]),
            }
            for e in events
        ]
        return MindmapReviewOutput(
            map_key=inp.map_key,
            root_memory_id=root_id,
            title=row["title"],
            state=row["state"],
            nodes=nodes,
            recent_events=recent,
            counter_reset_from=counter_was,
            request_id=ctx.request_id,
        )
