"""mindmap_write_node — capture a thinking node owned by a live map.

Writes a core memory (position/challenge/note), records exclusive membership,
bumps the reviewer write-counter, and carries the ratification gradient. Past a
soft byte threshold it raises an advisory split-suggested flag (split, never
trim — spec 5f439cd0). The node is always written; flags are advisory.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.mcp.tools._mindmap_common import resolve_team_id, write_node_memory
from mem_mcp.mindmap import RATIFICATION_STRENGTHS, SOFT_NODE_CHARS, service

_NodeRole = Literal["position", "challenge", "note", "question"]
_RatStrength = Literal["survived-challenge", "explicit-endorse", "delegate", "tacit"]


class NodeLink(BaseModel):
    target_memory_id: UUID
    reference_kind: str = Field(..., min_length=1, max_length=128)


class MindmapWriteNodeInput(BaseModel):
    map_key: str = Field(..., min_length=1, max_length=64)
    content: str = Field(..., min_length=1, max_length=32000)
    node_role: _NodeRole = "position"
    team_id: UUID | None = None
    responsible_party: Literal["owner", "model"] | None = None
    authored_by: Literal["owner", "model"] | None = Field(
        default=None,
        description=(
            "Who WROTE this node, as distinct from responsible_party (whose turn "
            "it is next). Set 'owner' for human-authored contributions so the "
            "graduation guard can refuse to settle a map past unread human input."
        ),
    )
    ratification_strength: _RatStrength | None = None
    ratification_citation: str | None = Field(default=None, max_length=4000)
    links: list[NodeLink] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    decision_criteria: str | None = Field(
        default=None,
        max_length=4000,
        description=(
            "For node_role='question': what you will do with each possible answer. "
            "Written now, while the reasoning is still in context and therefore free; "
            "read later by a cold agent that no longer holds the branch."
        ),
    )


class MindmapWriteNodeOutput(BaseModel):
    node_id: UUID
    map_key: str
    node_role: str
    writes_since_review: int
    review_due: bool
    split_suggested: bool
    request_id: str


class MindmapWriteNodeTool(BaseTool):
    name: ClassVar[str] = "mindmap_write_node"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["mindmap_write_node"]
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = MindmapWriteNodeInput
    OutputModel: ClassVar[type[BaseModel]] = MindmapWriteNodeOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MindmapWriteNodeInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        # Resolve the map + assert it is live (no adding to an archived map).
        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            team_id = await resolve_team_id(conn, ctx, inp.team_id)
            root_id = await service.resolve_map_root(conn, team_id=team_id, map_key=inp.map_key)
            if root_id is None:
                raise JsonRpcError(-32602, "map not found", data={"errors": [{"path": "map_key"}]})
            row = await service.get_map_row(conn, root_memory_id=root_id)
            if row is None or row["state"] != "live":
                raise JsonRpcError(
                    -32602,
                    "map is archived; closed maps do not accept new nodes",
                    data={"code": "map_not_live"},
                )

        # Build node metadata (drop None values).
        meta: dict[str, Any] = {**inp.metadata, "node_role": inp.node_role}
        if inp.responsible_party is not None:
            meta["responsible_party"] = inp.responsible_party
        if inp.ratification_strength is not None:
            meta["ratification_strength"] = inp.ratification_strength
        if inp.ratification_citation is not None:
            meta["ratification_citation"] = inp.ratification_citation

        mem_type = (
            inp.node_role if inp.node_role in ("position", "challenge", "question") else "note"
        )
        refs = [
            {
                "target_uuid": link.target_memory_id,
                "reference_kind": link.reference_kind,
                "refs_version": "pinned",
            }
            for link in inp.links
        ] or None

        node_out = await write_node_memory(
            ctx,
            content=inp.content,
            mem_type=mem_type,
            team_id=team_id,
            metadata=meta,
            references=refs,
        )
        node_id = node_out.id

        split_suggested = len(inp.content) > SOFT_NODE_CHARS

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            await service.insert_membership(
                conn,
                memory_id=node_id,
                root_memory_id=root_id,
                tenant_id=ctx.tenant_id,
                node_role=inp.node_role,
                turn=inp.responsible_party,
                authored_by=inp.authored_by,
                decision_criteria=inp.decision_criteria,
            )
            # Watermark owner writes so mindmap_close can refuse to graduate past
            # human input the agent never read. Keyed on AUTHORSHIP, not turn: an
            # agent asking a question sets responsible_party='owner' and must not
            # trip the guard on its own question.
            if inp.authored_by == "owner":
                await service.touch_owner_write(conn, root_memory_id=root_id)
            count, threshold = await service.bump_review_counter(conn, root_memory_id=root_id)
            await service.log_event(
                conn,
                root_memory_id=root_id,
                tenant_id=ctx.tenant_id,
                event="ratify" if inp.ratification_strength else "capture",
                actor="model",
                memory_id=node_id,
                payload={
                    "node_role": inp.node_role,
                    "ratification_strength": inp.ratification_strength,
                },
            )
            if split_suggested:
                await service.log_event(
                    conn,
                    root_memory_id=root_id,
                    tenant_id=ctx.tenant_id,
                    event="split_flag",
                    actor="reviewer",
                    memory_id=node_id,
                    payload={"chars": len(inp.content), "soft_limit": SOFT_NODE_CHARS},
                )

        review_due = count >= threshold
        if review_due:
            async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
                await service.log_event(
                    conn,
                    root_memory_id=root_id,
                    tenant_id=ctx.tenant_id,
                    event="review_flag",
                    actor="reviewer",
                    payload={"writes_since_review": count, "threshold": threshold},
                )

        # validate the gradient value defensively (belt + suspenders).
        if inp.ratification_strength and inp.ratification_strength not in RATIFICATION_STRENGTHS:
            raise JsonRpcError(-32602, "invalid ratification_strength")

        return MindmapWriteNodeOutput(
            node_id=node_id,
            map_key=inp.map_key,
            node_role=inp.node_role,
            writes_since_review=count,
            review_due=review_due,
            split_suggested=split_suggested,
            request_id=ctx.request_id,
        )
