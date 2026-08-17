"""mindmap_answer — answer an open question and settle its branch in one call.

Answering and resolving are the same act. Splitting them across two verbs
guarantees drift: someone answers, forgets to resolve, and the question sits in
the queue forever looking unanswered — which is the failure the branch-status
work exists to remove.

This is also the verb the web UI calls. A human answering out of band writes
``answered_by='owner'``, which does three things the agent-side path cannot fake:
records genuine authorship, hands the turn back to the model, and watermarks the
map so ``mindmap_close`` refuses to graduate past input the agent never read.
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

# Long answers still need a one-line stand-in for every later cheap read.
_SUMMARY_CAP = 200


class MindmapAnswerInput(BaseModel):
    map_key: str = Field(..., min_length=1, max_length=64)
    question_node_id: UUID
    content: str = Field(..., min_length=1, max_length=32000)
    answered_by: Literal["owner", "model"] = "owner"
    # When the human endorses rather than merely replies, record it as
    # first-party evidence rather than the model asserting it on their behalf.
    ratification_strength: (
        Literal["survived-challenge", "explicit-endorse", "delegate", "tacit"] | None
    ) = None
    ratification_citation: str | None = Field(default=None, max_length=4000)
    resolution_summary: str | None = Field(default=None, max_length=500)
    # How answering affects the QUESTION's status. The tool never refuses an
    # answer based on status; the caller declares intent:
    #   resolve — mark the question resolved (settle it)
    #   reopen  — put the question back to 'open', hand the turn to the other party
    #   comment — add the answer but leave the question's status untouched
    mode: Literal["resolve", "reopen", "comment"] | None = None
    # Deprecated alias for `mode`. auto_resolve=True → "resolve", False → "reopen".
    # If both are set, `mode` wins.
    auto_resolve: bool = True
    team_id: UUID | None = None


class MindmapAnswerOutput(BaseModel):
    answer_node_id: UUID
    question_node_id: UUID
    question_status: str
    resolution_summary: str | None
    cursor: int
    request_id: str


class MindmapAnswerTool(BaseTool):
    name: ClassVar[str] = "mindmap_answer"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["mindmap_answer"]
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = MindmapAnswerInput
    OutputModel: ClassVar[type[BaseModel]] = MindmapAnswerOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MindmapAnswerInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        # Validate everything BEFORE writing the answer node. Creating the node
        # first and then discovering the question is bogus would leave an
        # orphan reply attached to nothing.
        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            team_id = await resolve_team_id(conn, ctx, inp.team_id)
            root_id = await service.resolve_map_root(conn, team_id=team_id, map_key=inp.map_key)
            if root_id is None:
                raise JsonRpcError(-32602, "map not found", data={"errors": [{"path": "map_key"}]})
            row = await service.get_map_row(conn, root_memory_id=root_id)
            if row is None or row["state"] != "live":
                raise JsonRpcError(
                    -32602,
                    "map is archived; archived maps are no-reopen",
                    data={"code": "map_not_live"},
                )
            question = await conn.fetchrow(
                """
                SELECT status, node_role FROM memory_map_membership
                WHERE memory_id = $1 AND root_memory_id = $2
                """,
                inp.question_node_id,
                root_id,
            )
            if question is None:
                raise JsonRpcError(
                    -32602,
                    "question node is not a member of this map",
                    data={"errors": [{"path": "question_node_id"}]},
                )
            # NOTE: no status gate here on purpose. Answering is the caller's
            # prerogative, not the tool's — a resolved or dropped question can be
            # answered again (see `mode`). Only integrity guards remain: the map
            # must be live and the node must be a member of this map.

        # The caller declares what answering does to the question's status.
        # `mode` wins over the deprecated `auto_resolve` boolean.
        mode = inp.mode if inp.mode is not None else ("resolve" if inp.auto_resolve else "reopen")

        meta: dict[str, Any] = {"node_role": "note", "responsible_party": "model"}
        if inp.ratification_strength is not None:
            meta["ratification_strength"] = inp.ratification_strength
        if inp.ratification_citation is not None:
            meta["ratification_citation"] = inp.ratification_citation

        node_out = await write_node_memory(
            ctx,
            content=inp.content,
            mem_type="note",
            team_id=team_id,
            metadata=meta,
            references=[
                {
                    "target_uuid": inp.question_node_id,
                    "reference_kind": "answers",
                    "refs_version": "pinned",
                }
            ],
        )
        answer_id = node_out.id

        summary = inp.resolution_summary or _summarise(inp.content)

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            await service.insert_membership(
                conn,
                memory_id=answer_id,
                root_memory_id=root_id,
                tenant_id=ctx.tenant_id,
                node_role="note",
                # The turn goes BACK to the model: the owner has moved. Leaving
                # it 'owner' would park the map in their queue waiting on a move
                # they just made.
                turn="model",
                authored_by=inp.answered_by,
            )
            if inp.answered_by == "owner":
                await service.touch_owner_write(conn, root_memory_id=root_id)

            status = question["status"]
            summary_out: str | None = None
            if mode == "resolve":
                await service.set_node_status(
                    conn,
                    memory_id=inp.question_node_id,
                    root_memory_id=root_id,
                    status="resolved",
                    resolution_summary=summary,
                    resolved_by_memory_id=answer_id,
                    resolved_by_party=inp.answered_by,
                )
                status = "resolved"
                summary_out = summary
            elif mode == "reopen":
                # The owner has more to say: put the question back in play and
                # hand the turn to the other party. Prior resolution is cleared.
                next_turn = "model" if inp.answered_by == "owner" else "owner"
                await service.reopen_node(
                    conn,
                    memory_id=inp.question_node_id,
                    root_memory_id=root_id,
                    turn=next_turn,
                )
                status = "open"
            # mode == "comment": leave the question's status/resolution untouched.

            await service.log_event(
                conn,
                root_memory_id=root_id,
                tenant_id=ctx.tenant_id,
                event="answer",
                actor=inp.answered_by,
                memory_id=answer_id,
                payload={
                    "question_node_id": str(inp.question_node_id),
                    "mode": mode,
                    "ratification_strength": inp.ratification_strength,
                },
            )
            cursor = await service.current_cursor(conn, root_memory_id=root_id)

        return MindmapAnswerOutput(
            answer_node_id=answer_id,
            question_node_id=inp.question_node_id,
            question_status=status,
            resolution_summary=summary_out,
            cursor=cursor,
            request_id=ctx.request_id,
        )


def _summarise(content: str) -> str:
    """One-line stand-in for the answer in later cheap reads.

    Deliberately dumb — first line, capped. The store is not the place to be
    clever about meaning, and a caller who wants a better summary passes
    resolution_summary explicitly.
    """
    first = content.strip().splitlines()[0].strip() if content.strip() else content
    return first if len(first) <= _SUMMARY_CAP else first[:_SUMMARY_CAP].rstrip() + "…"
