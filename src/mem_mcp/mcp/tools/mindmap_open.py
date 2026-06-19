"""mindmap_open — start a thinking map (cold or seeded from a spec node).

The map is rooted at a new `question` memory; that root is minted a `map`
slug which becomes the map's key (the memsys "key", per owner decision). For
from_spec maps, an exact dedup guard refuses to open a second LIVE map already
seeded from the same source node.
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
from mem_mcp.mcp.tools._mindmap_common import resolve_team_id, write_node_memory
from mem_mcp.mindmap import DEFAULT_REVIEW_THRESHOLD, service
from mem_mcp.teams.slugs import (
    SlugClueInvalidError,
    SlugClueReservedError,
    insert_slug_with_retry,
    normalize_slug_clue,
)


class MindmapOpenInput(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    root_question: str = Field(..., min_length=1, max_length=8000)
    team_id: UUID | None = None
    seed_memory_id: UUID | None = Field(
        default=None,
        description="UUID of an existing KB memory to seed this map from (from_spec).",
    )
    review_threshold: int = Field(default=DEFAULT_REVIEW_THRESHOLD, ge=1, le=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MindmapOpenOutput(BaseModel):
    map_key: str
    root_memory_id: UUID
    title: str
    state: str
    deduped: bool
    seeded_from: UUID | None
    review_threshold: int
    request_id: str


class MindmapOpenTool(BaseTool):
    name: ClassVar[str] = "mindmap_open"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["mindmap_open"]
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = MindmapOpenInput
    OutputModel: ClassVar[type[BaseModel]] = MindmapOpenOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MindmapOpenInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        # Fail fast if the title can't make a slug — avoids orphaning a root
        # memory when the map key can't be minted afterwards.
        if normalize_slug_clue(inp.title) is None:
            raise JsonRpcError(
                -32602,
                "title does not yield a valid map key; use ASCII letters/digits",
                data={"errors": [{"path": "title", "message": "unsluggable title"}]},
            )

        # Pre-checks (read-only): resolve team + from_spec dedup guard, BEFORE
        # creating the root memory.
        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            team_id = await resolve_team_id(conn, ctx, inp.team_id)
            if inp.seed_memory_id is not None:
                existing_root = await service.find_live_map_seeded_from(
                    conn, seed_spec_memory_id=inp.seed_memory_id
                )
                if existing_root is not None:
                    existing = await service.get_map_row(conn, root_memory_id=existing_root)
                    key = await conn.fetchval(
                        "SELECT slug FROM slugs WHERE resource_type = 'map' AND memory_id = $1",
                        existing_root,
                    )
                    return MindmapOpenOutput(
                        map_key=str(key),
                        root_memory_id=existing_root,
                        title=existing["title"] if existing else inp.title,
                        state="live",
                        deduped=True,
                        seeded_from=inp.seed_memory_id,
                        review_threshold=existing["review_threshold"] if existing else inp.review_threshold,
                        request_id=ctx.request_id,
                    )

        # Create the root question memory (with a seeded-from edge if from_spec).
        root_meta = {**inp.metadata, "node_role": "root", "map_title": inp.title}
        refs = None
        if inp.seed_memory_id is not None:
            refs = [
                {
                    "target_uuid": inp.seed_memory_id,
                    "reference_kind": "seeded-from",
                    "refs_version": "pinned",
                }
            ]
        root_out = await write_node_memory(
            ctx,
            content=inp.root_question,
            mem_type="question",
            team_id=team_id,
            metadata=root_meta,
            references=refs,
        )
        root_id = root_out.id

        # Mint the map key + create the map graph rows.
        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            try:
                map_key = await insert_slug_with_retry(
                    conn,
                    team_id=team_id,
                    resource_type="map",
                    clue=inp.title,
                    memory_id=root_id,
                    title=inp.title,
                )
            except (SlugClueInvalidError, SlugClueReservedError) as exc:
                raise JsonRpcError(
                    -32602, "could not mint map key", data={"message": str(exc)[:200]}
                ) from exc

            await service.insert_map(
                conn,
                root_memory_id=root_id,
                tenant_id=ctx.tenant_id,
                team_id=team_id,
                title=inp.title,
                review_threshold=inp.review_threshold,
                seed_spec_memory_id=inp.seed_memory_id,
            )
            await service.insert_membership(
                conn,
                memory_id=root_id,
                root_memory_id=root_id,
                tenant_id=ctx.tenant_id,
                node_role="root",
            )
            await service.log_event(
                conn,
                root_memory_id=root_id,
                tenant_id=ctx.tenant_id,
                event="open",
                actor="model",
                memory_id=root_id,
                payload={"title": inp.title, "seeded_from": str(inp.seed_memory_id)
                         if inp.seed_memory_id else None},
            )

        return MindmapOpenOutput(
            map_key=map_key,
            root_memory_id=root_id,
            title=inp.title,
            state="live",
            deduped=False,
            seeded_from=inp.seed_memory_id,
            review_threshold=inp.review_threshold,
            request_id=ctx.request_id,
        )
