"""mindmap_get — read a map's graph: root, nodes, edges, flags, open loops.

Read-only resume/inspect surface. Open loops = nodes whose turn is the owner's
(metadata.responsible_party == 'owner'), the spec's whose-turn resume surface.
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


class MindmapGetInput(BaseModel):
    map_key: str = Field(..., min_length=1, max_length=64)
    team_id: UUID | None = None


class MindmapNode(BaseModel):
    memory_id: UUID
    node_role: str
    type: str
    content: str
    metadata: dict[str, Any]
    created_at: datetime


class MindmapEdge(BaseModel):
    source_memory_id: UUID
    target_memory_id: UUID
    reference_kind: str


class MindmapGetOutput(BaseModel):
    map_key: str
    root_memory_id: UUID
    title: str
    state: str
    writes_since_review: int
    review_threshold: int
    review_due: bool
    nodes: list[MindmapNode]
    edges: list[MindmapEdge]
    open_loops: list[UUID]
    request_id: str


class MindmapGetTool(BaseTool):
    name: ClassVar[str] = "mindmap_get"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["mindmap_get"]
    required_scope: ClassVar[str] = "memory.read"
    InputModel: ClassVar[type[BaseModel]] = MindmapGetInput
    OutputModel: ClassVar[type[BaseModel]] = MindmapGetOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MindmapGetInput)

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            team_id = await resolve_team_id(conn, ctx, inp.team_id)
            root_id = await service.resolve_map_root(conn, team_id=team_id, map_key=inp.map_key)
            if root_id is None:
                raise JsonRpcError(-32602, "map not found", data={"errors": [{"path": "map_key"}]})
            row = await service.get_map_row(conn, root_memory_id=root_id)
            assert row is not None
            members = await service.fetch_members(conn, root_memory_id=root_id, tenant_id=ctx.tenant_id)
            member_ids = [m["memory_id"] for m in members]
            edges = await service.fetch_internal_edges(conn, member_ids=member_ids)

        nodes = [
            MindmapNode(
                memory_id=m["memory_id"],
                node_role=m["node_role"],
                type=m["type"],
                content=m["content"],
                metadata=m["metadata"],
                created_at=m["created_at"],
            )
            for m in members
        ]
        open_loops = [
            m["memory_id"]
            for m in members
            if m["metadata"].get("responsible_party") == "owner"
        ]
        return MindmapGetOutput(
            map_key=inp.map_key,
            root_memory_id=root_id,
            title=row["title"],
            state=row["state"],
            writes_since_review=row["writes_since_review"],
            review_threshold=row["review_threshold"],
            review_due=row["writes_since_review"] >= row["review_threshold"],
            nodes=nodes,
            edges=[
                MindmapEdge(
                    source_memory_id=e["source_memory_id"],
                    target_memory_id=e["target_memory_id"],
                    reference_kind=e["reference_kind"],
                )
                for e in edges
            ],
            open_loops=open_loops,
            request_id=ctx.request_id,
        )
