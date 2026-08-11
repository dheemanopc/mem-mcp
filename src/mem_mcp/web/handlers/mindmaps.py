"""Mind-map explorer + contributor — the web surface over the mindmap_* tools.

Every mindmap verb is an MCP tool reachable only over /mcp with OAuth scopes;
the web UI authenticates by httpOnly session cookie. These endpoints bridge the
two by building a ToolContext from the session and calling the SAME tools, so
lifecycle rules (archived maps are no-reopen, a question cannot be answered
twice, answering resolves) live in exactly one place. Reimplementing them here
is how the two provisioning paths drifted and left two users unable to log in.

The point of this surface is context economy, not visualisation. Walking a map
turn-by-turn in chat keeps every earlier branch resident while later branches are
worked. Answering several questions here, out of band, lets the agent re-enter
cold against a delta instead.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel, Field

from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._deps import ToolDeps
from mem_mcp.mcp.tools.mindmap_answer import MindmapAnswerInput, MindmapAnswerTool
from mem_mcp.mcp.tools.mindmap_get import MindmapGetInput, MindmapGetTool
from mem_mcp.mcp.tools.mindmap_queue import (
    MindmapListInput,
    MindmapListTool,
    MindmapQueueInput,
    MindmapQueueTool,
)
from mem_mcp.mcp.tools.mindmap_resolve import MindmapResolveInput, MindmapResolveTool
from mem_mcp.web.sessions import lookup_session

_READ = frozenset({"memory.read"})
_WRITE = frozenset({"memory.read", "memory.write"})


async def _ctx_from_session(
    pool: asyncpg.Pool,
    mem_session: str | None,
    deps: ToolDeps,
    *,
    scopes: frozenset[str],
) -> ToolContext:
    if not mem_session:
        raise HTTPException(status_code=401, detail="not authenticated")
    sess = await lookup_session(pool, mem_session)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid session")
    return ToolContext(
        request_id=str(uuid4()),
        tenant_id=sess.tenant_id,
        identity_id=sess.identity_id,
        client_id="web",
        scopes=scopes,
        db_pool=pool,
        deps=deps,
    )


def _http_from_jsonrpc(exc: JsonRpcError) -> HTTPException:
    """Surface the tool's own refusal rather than flattening it to a 500.

    The lifecycle errors are the interesting ones — "already resolved", "map
    archived", "unacknowledged owner input" — and the UI needs to tell them
    apart to say anything useful.
    """
    data = exc.data if isinstance(exc.data, dict) else {}
    return HTTPException(
        status_code=400,
        detail={"message": str(exc.message), "code": data.get("code"), "data": data},
    )


class AnswerBody(BaseModel):
    question_node_id: UUID
    content: str = Field(..., min_length=1, max_length=32000)
    # Defaults to owner: a human is the one clicking. answered_by is what makes
    # the graduation guard able to refuse settling a map past this input.
    answered_by: Literal["owner", "model"] = "owner"
    ratification_strength: (
        Literal["survived-challenge", "explicit-endorse", "delegate", "tacit"] | None
    ) = None
    team_id: UUID | None = None


class ResolveBody(BaseModel):
    node_id: UUID
    disposition: Literal["resolved", "dropped"] = "resolved"
    resolution_summary: str = Field(..., min_length=1, max_length=500)
    resolved_by_memory_id: UUID | None = None
    team_id: UUID | None = None


def make_mindmaps_router(*, pool: asyncpg.Pool, deps: ToolDeps) -> APIRouter:
    """Factory for the /api/web/mindmaps router."""
    router = APIRouter()

    @router.get("/api/web/mindmaps")
    async def list_maps(
        state: Literal["live", "archived", "all"] = "live",
        has_open_loops: bool | None = None,
        team_id: UUID | None = None,
        mem_session: str | None = Cookie(default=None),
    ) -> Any:
        ctx = await _ctx_from_session(pool, mem_session, deps, scopes=_READ)
        try:
            out = await MindmapListTool()(
                ctx,
                MindmapListInput(state=state, has_open_loops=has_open_loops, team_id=team_id),
            )
        except JsonRpcError as exc:
            raise _http_from_jsonrpc(exc) from exc
        return out.model_dump(mode="json")

    @router.get("/api/web/mindmaps/queue")
    async def queue(
        team_id: UUID | None = None,
        mem_session: str | None = Cookie(default=None),
    ) -> Any:
        """Every question awaiting the owner, across all live maps.

        Registered before /{map_key} so "queue" is not swallowed as a map key.
        """
        ctx = await _ctx_from_session(pool, mem_session, deps, scopes=_READ)
        try:
            out = await MindmapQueueTool()(ctx, MindmapQueueInput(team_id=team_id))
        except JsonRpcError as exc:
            raise _http_from_jsonrpc(exc) from exc
        return out.model_dump(mode="json")

    @router.get("/api/web/mindmaps/{map_key}")
    async def get_map(
        map_key: str,
        team_id: UUID | None = None,
        include: Literal["open", "all"] = "all",
        depth: Literal["summary", "full"] = "full",
        mem_session: str | None = Cookie(default=None),
    ) -> Any:
        """Read one map.

        Defaults are include=all/depth=full — the OPPOSITE of the agent-facing
        defaults, and deliberately so. The cheap defaults exist to keep an
        agent's context small; a human reading one map in a browser wants the
        whole thing, and no context is being consumed.
        """
        ctx = await _ctx_from_session(pool, mem_session, deps, scopes=_READ)
        try:
            out = await MindmapGetTool()(
                ctx,
                MindmapGetInput(map_key=map_key, team_id=team_id, include=include, depth=depth),
            )
        except JsonRpcError as exc:
            raise _http_from_jsonrpc(exc) from exc
        return out.model_dump(mode="json")

    @router.post("/api/web/mindmaps/{map_key}/answer")
    async def answer(
        map_key: str,
        body: AnswerBody,
        mem_session: str | None = Cookie(default=None),
    ) -> Any:
        ctx = await _ctx_from_session(pool, mem_session, deps, scopes=_WRITE)
        try:
            out = await MindmapAnswerTool()(
                ctx,
                MindmapAnswerInput(
                    map_key=map_key,
                    question_node_id=body.question_node_id,
                    content=body.content,
                    answered_by=body.answered_by,
                    ratification_strength=body.ratification_strength,
                    # The human's own words ARE the citation here — no model is
                    # paraphrasing them, which is the whole point of answering
                    # from the UI rather than through a transcript.
                    ratification_citation=(
                        body.content[:4000] if body.ratification_strength else None
                    ),
                    team_id=body.team_id,
                ),
            )
        except JsonRpcError as exc:
            raise _http_from_jsonrpc(exc) from exc
        return out.model_dump(mode="json")

    @router.post("/api/web/mindmaps/{map_key}/resolve")
    async def resolve(
        map_key: str,
        body: ResolveBody,
        mem_session: str | None = Cookie(default=None),
    ) -> Any:
        ctx = await _ctx_from_session(pool, mem_session, deps, scopes=_WRITE)
        try:
            out = await MindmapResolveTool()(
                ctx,
                MindmapResolveInput(
                    map_key=map_key,
                    node_id=body.node_id,
                    disposition=body.disposition,
                    resolution_summary=body.resolution_summary,
                    resolved_by_memory_id=body.resolved_by_memory_id,
                    resolved_by_party="owner",
                    team_id=body.team_id,
                ),
            )
        except JsonRpcError as exc:
            raise _http_from_jsonrpc(exc) from exc
        return out.model_dump(mode="json")

    return router
