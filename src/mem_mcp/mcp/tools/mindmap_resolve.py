"""mindmap_resolve — settle one branch of a map without closing the map.

``mindmap_close`` graduates and archives the WHOLE map. There was no per-branch
equivalent, so every branch stayed live in every read forever and the agent
re-loaded settled discussion on every resume. This is the verb that lets a map
be walked without its cost growing with total discussion volume.

Writes the status AND the edge in one transaction. Status is the queryable
truth (indexed, constrained); the edge remains so the graph stays walkable.
Deriving "settled" from ``reference_kind`` alone was rejected: that column is
unvalidated free text, and the read path that decides what enters an agent's
context must not depend on string-matching a field nothing enforces.
"""

from __future__ import annotations

from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.mcp.tools._mindmap_common import resolve_team_id
from mem_mcp.mindmap import service
from mem_mcp.teams.references import insert_reference

_EDGE_KIND = {"resolved": "resolves-under", "dropped": "dropped-under"}


class MindmapResolveInput(BaseModel):
    map_key: str = Field(..., min_length=1, max_length=64)
    node_id: UUID
    disposition: Literal["resolved", "dropped"] = "resolved"
    # Mandatory: a branch that cannot be summarised in a line is not settled,
    # and this string is what every future cheap read shows in its place.
    resolution_summary: str = Field(..., min_length=1, max_length=500)
    resolved_by_memory_id: UUID | None = None
    resolved_by_party: Literal["owner", "model"] = "model"
    team_id: UUID | None = None


class MindmapResolveOutput(BaseModel):
    node_id: UUID
    status: str
    resolution_summary: str
    cursor: int
    request_id: str


class MindmapResolveTool(BaseTool):
    name: ClassVar[str] = "mindmap_resolve"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["mindmap_resolve"]
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = MindmapResolveInput
    OutputModel: ClassVar[type[BaseModel]] = MindmapResolveOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MindmapResolveInput)

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            team_id = await resolve_team_id(conn, ctx, inp.team_id)
            root_id = await service.resolve_map_root(conn, team_id=team_id, map_key=inp.map_key)
            if root_id is None:
                raise JsonRpcError(-32602, "map not found", data={"errors": [{"path": "map_key"}]})
            row = await service.get_map_row(conn, root_memory_id=root_id)
            assert row is not None
            if row["state"] != "live":
                raise JsonRpcError(
                    -32602,
                    "map is archived; archived maps are no-reopen",
                    data={"errors": [{"path": "map_key", "message": "map not live"}]},
                )

            updated = await service.set_node_status(
                conn,
                memory_id=inp.node_id,
                root_memory_id=root_id,
                status=inp.disposition,
                resolution_summary=inp.resolution_summary,
                resolved_by_memory_id=inp.resolved_by_memory_id,
                resolved_by_party=inp.resolved_by_party,
            )
            if not updated:
                raise JsonRpcError(
                    -32602,
                    "node is not a member of this map",
                    data={"errors": [{"path": "node_id"}]},
                )

            # The edge is written only when a resolving node was named; a branch
            # can be dropped without anything resolving it. Goes through the
            # canonical insert_reference so the edge is byte-for-byte what
            # mindmap_link would have written.
            if inp.resolved_by_memory_id is not None:
                await insert_reference(
                    conn,
                    source_memory_id=inp.resolved_by_memory_id,
                    target_memory_id=inp.node_id,
                    target_team_id=team_id,
                    reference_kind=_EDGE_KIND[inp.disposition],
                )

            await service.log_event(
                conn,
                root_memory_id=root_id,
                tenant_id=ctx.tenant_id,
                event="resolve",
                actor=inp.resolved_by_party,
                memory_id=inp.node_id,
                payload={
                    "disposition": inp.disposition,
                    "resolution_summary": inp.resolution_summary,
                },
            )
            cursor = await service.current_cursor(conn, root_memory_id=root_id)

        return MindmapResolveOutput(
            node_id=inp.node_id,
            status=inp.disposition,
            resolution_summary=inp.resolution_summary,
            cursor=cursor,
            request_id=ctx.request_id,
        )
