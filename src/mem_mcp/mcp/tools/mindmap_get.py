"""mindmap_get — read a map's graph: root, nodes, edges, flags, open loops.

Read-only resume/inspect surface, and the read path the whole context-economy
story rests on. Three levers, all defaulting to the cheap setting:

* ``since=<cursor>`` — only what changed since a previous call. The cursor is
  ``memory_map_events.id``, already a BIGSERIAL on an append-only log.
* ``include="open"`` — open branches in full; settled ones as one-line
  ``resolution_summary`` entries instead of their whole discussion.
* ``depth="summary"`` — cap node text, with ``truncated`` flagged so a caller
  can refetch deliberately.

Open loops are UNRESOLVED nodes whose turn is the owner's (``turn='owner'`` AND
``status='open'``). The previous implementation filtered on turn alone, so it
reported every owner-turn node forever and the list only ever grew; ``status``
is what retires an answered ask.

Note ``turn`` means whose MOVE it is, not who wrote the node: a reply authored
by the owner sets ``responsible_party='model'`` to hand the turn back.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.mcp.tools._mindmap_common import resolve_team_id
from mem_mcp.mindmap import service

SUMMARY_CHARS = 200


def _is_truncated(content: str, depth: str) -> bool:
    return depth == "summary" and len(content) > SUMMARY_CHARS


def _maybe_truncate(content: str, depth: str) -> str:
    """Cap node text on the cheap read; `depth="full"` returns it whole.

    Truncation is the difference between a resume costing the whole discussion
    and costing an index of it. Callers that need the full text ask for it.
    """
    if not _is_truncated(content, depth):
        return content
    return content[:SUMMARY_CHARS].rstrip() + "…"


class MindmapGetInput(BaseModel):
    map_key: str = Field(..., min_length=1, max_length=64)
    team_id: UUID | None = None
    since: int | None = Field(
        default=None,
        ge=0,
        description="Event-id cursor from a previous call; returns only what changed since.",
    )
    include: Literal["open", "all"] = "open"
    depth: Literal["summary", "full"] = "summary"


class MindmapNode(BaseModel):
    memory_id: UUID
    node_role: str
    type: str
    content: str
    metadata: dict[str, Any]
    created_at: datetime
    status: str = "open"
    turn: str | None = None
    decision_criteria: str | None = None
    truncated: bool = False


class ResolvedBranch(BaseModel):
    """A settled branch, compacted to one line so it costs ~20 tokens not ~800."""

    memory_id: UUID
    resolution_summary: str | None
    status: str
    resolved_by_party: str | None


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
    cursor: int
    nodes: list[MindmapNode]
    resolved: list[ResolvedBranch]
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

            # Delta: restrict to nodes touched since the caller's cursor. An
            # empty id list means nothing changed — fetch_members then returns
            # nothing rather than everything.
            since_ids: list[UUID] | None = None
            if inp.since is not None:
                since_ids, cursor = await service.fetch_memory_ids_since(
                    conn, root_memory_id=root_id, since=inp.since
                )
            else:
                cursor = await service.current_cursor(conn, root_memory_id=root_id)

            statuses = ["open"] if inp.include == "open" else None
            members = await service.fetch_members(
                conn,
                root_memory_id=root_id,
                tenant_id=ctx.tenant_id,
                statuses=statuses,
                since_memory_ids=since_ids,
            )
            # Settled branches come back as one-line summaries, never full text.
            # Skipped entirely on a delta read: a branch resolved before the
            # cursor is already known to the caller.
            settled: list[dict[str, Any]] = []
            if inp.include == "open" and inp.since is None:
                settled = await service.fetch_members(
                    conn,
                    root_memory_id=root_id,
                    tenant_id=ctx.tenant_id,
                    statuses=["resolved", "dropped"],
                )
            member_ids = [m["memory_id"] for m in members]
            edges = await service.fetch_internal_edges(conn, member_ids=member_ids)
            await service.touch_agent_read(conn, root_memory_id=root_id)

        nodes = [
            MindmapNode(
                memory_id=m["memory_id"],
                node_role=m["node_role"],
                type=m["type"],
                content=_maybe_truncate(m["content"], inp.depth),
                metadata=m["metadata"],
                created_at=m["created_at"],
                status=m["status"],
                turn=m["turn"],
                decision_criteria=m["decision_criteria"],
                truncated=_is_truncated(m["content"], inp.depth),
            )
            for m in members
        ]
        resolved = [
            ResolvedBranch(
                memory_id=m["memory_id"],
                resolution_summary=m["resolution_summary"],
                status=m["status"],
                resolved_by_party=m["resolved_by_party"],
            )
            for m in settled
        ]
        # An open loop is an UNRESOLVED node whose turn is the owner's. The
        # previous implementation checked turn alone, so it reported every
        # owner-turn node forever and only ever grew; `status` is what closes
        # the loop.
        #
        # `turn` means WHOSE MOVE IT IS, not who authored the node — so a reply
        # written by the owner hands the turn back with responsible_party
        # 'model'. Leaving it 'owner' would park the map in the owner's inbox
        # forever waiting on a move they already made.
        open_loops = [
            m["memory_id"] for m in members if m["turn"] == "owner" and m["status"] == "open"
        ]
        return MindmapGetOutput(
            map_key=inp.map_key,
            root_memory_id=root_id,
            cursor=cursor,
            resolved=resolved,
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
