"""mindmap_list / mindmap_queue — enumerate maps, and every question awaiting the owner.

There was no enumeration verb at all: maps could be opened and read by key, but
not discovered. That blocks both an explorer UI and — more importantly — the
cross-map answer queue.

The queue is the point. Walking a map turn-by-turn inside one session keeps
every earlier branch resident in context while later branches are worked. If
instead the agent posts its open questions and stops, the owner can answer many
of them out of band, in parallel, across several maps, and the agent re-enters
cold against a delta. ``mindmap_queue`` is what makes "answer several threads at
once" expressible.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.mindmap import service


class MindmapListInput(BaseModel):
    team_id: UUID | None = None
    state: Literal["live", "archived", "all"] = "live"
    has_open_loops: bool | None = None
    limit: int = Field(default=25, ge=1, le=100)


class MapSummary(BaseModel):
    map_key: str
    root_memory_id: UUID
    title: str
    state: str
    open_loop_count: int
    last_owner_write_at: datetime | None
    created_at: datetime


class MindmapListOutput(BaseModel):
    maps: list[MapSummary]
    request_id: str


class MindmapListTool(BaseTool):
    name: ClassVar[str] = "mindmap_list"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["mindmap_list"]
    required_scope: ClassVar[str] = "memory.read"
    InputModel: ClassVar[type[BaseModel]] = MindmapListInput
    OutputModel: ClassVar[type[BaseModel]] = MindmapListOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MindmapListInput)
        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            rows = await service.list_maps(
                conn,
                tenant_id=ctx.tenant_id,
                team_id=inp.team_id,
                state=inp.state,
                has_open_loops=inp.has_open_loops,
                limit=inp.limit,
            )
        return MindmapListOutput(
            maps=[
                MapSummary(
                    map_key=r["map_key"],
                    root_memory_id=r["root_memory_id"],
                    title=r["title"],
                    state=r["state"],
                    open_loop_count=int(r["open_loop_count"]),
                    last_owner_write_at=r["last_owner_write_at"],
                    created_at=r["created_at"],
                )
                for r in rows
            ],
            request_id=ctx.request_id,
        )


class MindmapQueueInput(BaseModel):
    team_id: UUID | None = None
    limit: int = Field(default=50, ge=1, le=200)


class QueuedQuestion(BaseModel):
    map_key: str
    map_title: str
    memory_id: UUID
    node_role: str
    content: str
    # Written when the question was asked, so it can be answered — and acted on
    # after a cold reload — without its branch in context.
    decision_criteria: str | None
    asked_at: datetime


class MindmapQueueOutput(BaseModel):
    questions: list[QueuedQuestion]
    request_id: str


class MindmapQueueTool(BaseTool):
    name: ClassVar[str] = "mindmap_queue"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["mindmap_queue"]
    required_scope: ClassVar[str] = "memory.read"
    InputModel: ClassVar[type[BaseModel]] = MindmapQueueInput
    OutputModel: ClassVar[type[BaseModel]] = MindmapQueueOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MindmapQueueInput)
        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            rows = await service.fetch_open_questions(
                conn, tenant_id=ctx.tenant_id, team_id=inp.team_id, limit=inp.limit
            )
        return MindmapQueueOutput(
            questions=[
                QueuedQuestion(
                    map_key=r["map_key"],
                    map_title=r["map_title"],
                    memory_id=r["memory_id"],
                    node_role=r["node_role"],
                    content=r["content"],
                    decision_criteria=r["decision_criteria"],
                    asked_at=r["added_at"],
                )
                for r in rows
            ],
            request_id=ctx.request_id,
        )
