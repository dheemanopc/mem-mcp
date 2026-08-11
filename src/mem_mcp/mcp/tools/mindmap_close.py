"""mindmap_close — graduate confirmed decisions to the KB, then archive.

Graduation (spec 6844c142 + d1ebfc6e): the model proposes significant
decisions, the HUMAN confirms (the second of two human touch-points), and each
confirmed decision becomes a NEW KB decision memory carrying a `promoted-from`
edge back to the archived map node. Promotion is a fresh write, never a
supersession. The map is then archived: cold and excluded from open-time
similarity search, but still walkable via references.

The 'delegate' ratification strength promotes tagged verify_substance=true —
the dangerous-to-mislabel case the gradient exists to catch (spec 526ca485).
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
from mem_mcp.mindmap import service

_RatStrength = Literal["survived-challenge", "explicit-endorse", "delegate", "tacit"]


class ConfirmedDecision(BaseModel):
    content: str = Field(..., min_length=1, max_length=32000)
    slug_clue: str = Field(..., min_length=1, max_length=200)
    from_node_id: UUID | None = Field(
        default=None, description="The archived map node this decision was promoted from."
    )
    ratification_strength: _RatStrength | None = None


class MindmapCloseInput(BaseModel):
    map_key: str = Field(..., min_length=1, max_length=64)
    team_id: UUID | None = None
    confirmed_decisions: list[ConfirmedDecision] = Field(default_factory=list)
    summary: str | None = Field(
        default=None, max_length=32000, description="Optional spec-index summary node content."
    )


class PromotedDecision(BaseModel):
    decision_id: UUID
    slug: str | None
    from_node_id: UUID | None
    verify_substance: bool


class MindmapCloseOutput(BaseModel):
    map_key: str
    state: str
    promoted: list[PromotedDecision]
    graduated_index_id: UUID | None
    request_id: str


class MindmapCloseTool(BaseTool):
    name: ClassVar[str] = "mindmap_close"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["mindmap_close"]
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = MindmapCloseInput
    OutputModel: ClassVar[type[BaseModel]] = MindmapCloseOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MindmapCloseInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            team_id = await resolve_team_id(conn, ctx, inp.team_id)
            root_id = await service.resolve_map_root(conn, team_id=team_id, map_key=inp.map_key)
            if root_id is None:
                raise JsonRpcError(-32602, "map not found", data={"errors": [{"path": "map_key"}]})
            row = await service.get_map_row(conn, root_memory_id=root_id)
            if row is None or row["state"] != "live":
                raise JsonRpcError(
                    -32602, "map already archived (no-reopen)", data={"code": "map_not_live"}
                )
            # Fail closed on unread human input. Graduation is irreversible
            # (archived maps are no-reopen), so a map must not be settled past
            # an owner node the agent never read — that would bury the one
            # human intervention behind a decision claiming to be ratified.
            unread = await service.unacknowledged_owner_input(
                conn, root_memory_id=root_id, tenant_id=ctx.tenant_id
            )
            if unread:
                raise JsonRpcError(
                    -32602,
                    "unacknowledged owner input: read the map before graduating",
                    data={
                        "code": "unacknowledged_owner_input",
                        "nodes": [
                            {"memory_id": str(u["memory_id"]), "content": u["content"][:500]}
                            for u in unread
                        ],
                    },
                )

        # Promote each confirmed decision to a NEW KB decision (not supersede).
        promoted: list[PromotedDecision] = []
        for dec in inp.confirmed_decisions:
            verify_substance = dec.ratification_strength == "delegate"
            refs = None
            if dec.from_node_id is not None:
                refs = [
                    {
                        "target_uuid": dec.from_node_id,
                        "reference_kind": "promoted-from",
                        "refs_version": "pinned",
                    }
                ]
            meta: dict[str, Any] = {
                "graduated": True,
                "promoted_from_map": inp.map_key,
                "verify_substance": verify_substance,
            }
            if dec.ratification_strength is not None:
                meta["ratification_strength"] = dec.ratification_strength
            dec_out = await write_node_memory(
                ctx,
                content=dec.content,
                mem_type="decision",
                team_id=team_id,
                metadata=meta,
                slug_clue=dec.slug_clue,
                references=refs,
            )
            promoted.append(
                PromotedDecision(
                    decision_id=dec_out.id,
                    slug=None,  # filled in below
                    from_node_id=dec.from_node_id,
                    verify_substance=verify_substance,
                )
            )

        # Optional spec-index summary node.
        graduated_index_id: UUID | None = None
        if inp.summary is not None:
            idx_out = await write_node_memory(
                ctx,
                content=inp.summary,
                mem_type="note",
                team_id=team_id,
                metadata={"map_index": True, "map_key": inp.map_key},
                references=[
                    {
                        "target_uuid": p.decision_id,
                        "reference_kind": "index-of",
                        "refs_version": "pinned",
                    }
                    for p in promoted
                ]
                or None,
            )
            graduated_index_id = idx_out.id

        # Resolve slugs for the promoted decisions, archive, and log.
        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            for p in promoted:
                p.slug = await conn.fetchval(
                    "SELECT slug FROM slugs WHERE resource_type = 'decision' AND memory_id = $1",
                    p.decision_id,
                )
                await service.log_event(
                    conn,
                    root_memory_id=root_id,
                    tenant_id=ctx.tenant_id,
                    event="promote",
                    actor="owner",
                    memory_id=p.decision_id,
                    payload={
                        "from_node_id": str(p.from_node_id) if p.from_node_id else None,
                        "verify_substance": p.verify_substance,
                        "slug": p.slug,
                    },
                )
            await service.archive_map(
                conn, root_memory_id=root_id, graduated_index_id=graduated_index_id
            )
            await service.log_event(
                conn,
                root_memory_id=root_id,
                tenant_id=ctx.tenant_id,
                event="close",
                actor="owner",
                payload={"promoted_count": len(promoted)},
            )

        return MindmapCloseOutput(
            map_key=inp.map_key,
            state="archived",
            promoted=promoted,
            graduated_index_id=graduated_index_id,
            request_id=ctx.request_id,
        )
