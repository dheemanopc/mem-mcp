"""End-to-end: branch status, delta reads, queue, and the graduation guard.

The unit tests for this feature assert on SQL *strings* against fake
connections. That catches shape, not validity — a query can be well-formed and
still reference a column that does not exist, or filter on the wrong row. Every
new statement added for branch status runs here against real Postgres.

The acceptance test at the bottom is the one that matters: it measures that a
delta resume is dramatically cheaper than a full read. That is the entire point
of the feature, and it is the only assertion that would notice if the plumbing
were correct but the saving never materialised.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest

from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools.mindmap_answer import MindmapAnswerInput, MindmapAnswerTool
from mem_mcp.mcp.tools.mindmap_close import MindmapCloseInput, MindmapCloseTool
from mem_mcp.mcp.tools.mindmap_get import MindmapGetInput, MindmapGetTool
from mem_mcp.mcp.tools.mindmap_open import MindmapOpenInput, MindmapOpenTool
from mem_mcp.mcp.tools.mindmap_queue import (
    MindmapListInput,
    MindmapListTool,
    MindmapQueueInput,
    MindmapQueueTool,
)
from mem_mcp.mcp.tools.mindmap_resolve import MindmapResolveInput, MindmapResolveTool
from mem_mcp.mcp.tools.mindmap_write_node import (
    MindmapWriteNodeInput,
    MindmapWriteNodeTool,
)
from mem_mcp.teams.assignments import assign_role

pytestmark = pytest.mark.asyncio


def _build_deps() -> Any:
    from mem_mcp.audit.logger import NoopAuditLogger
    from mem_mcp.mcp.tools._deps import NoopQuotas, ToolDeps

    class _StubEmbed:
        async def embed(self, content: str) -> Any:
            from mem_mcp.embeddings.bedrock import EmbedResult

            return EmbedResult(vector=[0.0] * 1024, input_tokens=max(1, len(content) // 4))

    return ToolDeps(embeddings=_StubEmbed(), audit=NoopAuditLogger(), quotas=NoopQuotas())


async def _setup(conn: asyncpg.Connection) -> dict[str, Any]:
    tenant = await conn.fetchval(
        "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
        f"mmstatus-{uuid4()}@example.test",
    )
    team = await conn.fetchval(
        "INSERT INTO teams (name, created_by_tenant_id) VALUES ($1, $2) RETURNING id",
        f"mmstatus-team-{uuid4().hex[:8]}",
        tenant,
    )
    await assign_role(
        conn,
        parent_team_id=team,
        member_kind="user",
        member_id=tenant,
        role_key="member",
        assigned_by_user_id=tenant,
    )
    await conn.execute(
        """
        INSERT INTO tenant_identities
            (tenant_id, cognito_sub, provider, email, is_primary, default_team_id)
        VALUES ($1, $2, 'cognito', $3, true, $4)
        """,
        tenant,
        f"sub-{tenant}",
        f"mmstatus-{tenant}@example.test",
        team,
    )
    client_id = f"client:mmstatus-{uuid4().hex[:8]}"
    await conn.execute(
        """
        INSERT INTO oauth_clients (id, redirect_uris, scope, registration_payload, review_status)
        VALUES ($1, ARRAY[]::text[], 'memory.read memory.write', '{}'::jsonb, 'auto_allowed')
        """,
        client_id,
    )
    return {"tenant": tenant, "team": team, "client_id": client_id}


async def _cleanup(pool: Any, tenant: UUID, team: UUID, client_id: str) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "DELETE FROM memory_references WHERE source_memory_id IN "
            "(SELECT id FROM memories WHERE tenant_id = $1) "
            "OR target_memory_id IN (SELECT id FROM memories WHERE tenant_id = $1)",
            tenant,
        )
        await conn.execute("DELETE FROM memory_map_events WHERE tenant_id = $1", tenant)
        await conn.execute("DELETE FROM memory_map_membership WHERE tenant_id = $1", tenant)
        await conn.execute("DELETE FROM memory_maps WHERE tenant_id = $1", tenant)
        await conn.execute("DELETE FROM slugs WHERE team_id = $1", team)
        await conn.execute("DELETE FROM memories WHERE tenant_id = $1", tenant)
        await conn.execute("DELETE FROM team_role_assignments WHERE parent_team_id = $1", team)
        await conn.execute("DELETE FROM tenant_identities WHERE tenant_id = $1", tenant)
        await conn.execute("DELETE FROM teams WHERE id = $1", team)
        await conn.execute("DELETE FROM tenants WHERE id = $1", tenant)
        await conn.execute("DELETE FROM oauth_clients WHERE id = $1", client_id)


def _ctx(pool: Any, tenant: UUID, client_id: str) -> ToolContext:
    return ToolContext(
        request_id=f"req-{uuid4().hex[:8]}",
        tenant_id=tenant,
        identity_id=tenant,
        client_id=client_id,
        scopes=frozenset({"memory.read", "memory.write"}),
        db_pool=pool,
        deps=_build_deps(),
    )


def _size(payload: Any) -> int:
    """Serialized byte size — a stand-in for token cost, and monotonic in it."""
    return len(json.dumps(payload, default=str))


async def test_branch_status_delta_and_queue(pg_pool: Any) -> None:
    async with pg_pool.acquire() as conn, conn.transaction():
        s = await _setup(conn)
    tenant, team, client_id = s["tenant"], s["team"], s["client_id"]
    ctx = _ctx(pg_pool, tenant, client_id)

    try:
        opened = await MindmapOpenTool()(
            ctx,
            MindmapOpenInput(
                title="Branch status e2e",
                root_question="Does resolving a branch make resume cheap?",
                team_id=team,
            ),
        )
        key = opened.map_key  # type: ignore[attr-defined]

        # Three questions awaiting the owner, each with decision criteria, plus
        # a long settled position whose text we want to stop paying for.
        q_ids = []
        for i in range(3):
            out = await MindmapWriteNodeTool()(
                ctx,
                MindmapWriteNodeInput(
                    map_key=key,
                    content=f"Question {i}: which way on branch {i}?",
                    node_role="question",
                    responsible_party="owner",
                    decision_criteria=f"if A then X{i}; if B then Y{i}",
                    team_id=team,
                ),
            )
            q_ids.append(out.node_id)  # type: ignore[attr-defined]

        long_pos = await MindmapWriteNodeTool()(
            ctx,
            MindmapWriteNodeInput(
                map_key=key,
                content="A settled position. " + ("verbose reasoning " * 200),
                node_role="position",
                responsible_party="model",
                team_id=team,
            ),
        )

        # --- open loops: exactly the three unanswered owner questions --------
        full = await MindmapGetTool()(ctx, MindmapGetInput(map_key=key, team_id=team))
        assert len(full.open_loops) == 3, full.open_loops  # type: ignore[attr-defined]
        assert set(full.open_loops) == set(q_ids)  # type: ignore[attr-defined]

        # decision_criteria survives the round trip — without it a cold agent
        # cannot act on an answer.
        crit = {n.memory_id: n.decision_criteria for n in full.nodes}  # type: ignore[attr-defined]
        assert crit[q_ids[0]] == "if A then X0; if B then Y0"

        # --- summary depth truncates the long node --------------------------
        long_node = next(n for n in full.nodes if n.memory_id == long_pos.node_id)  # type: ignore[attr-defined]
        assert long_node.truncated is True
        assert len(long_node.content) < 400

        deep = await MindmapGetTool()(ctx, MindmapGetInput(map_key=key, team_id=team, depth="full"))
        deep_node = next(n for n in deep.nodes if n.memory_id == long_pos.node_id)  # type: ignore[attr-defined]
        assert deep_node.truncated is False
        assert len(deep_node.content) > 3000

        # --- resolve two branches -------------------------------------------
        for i in (0, 1):
            await MindmapResolveTool()(
                ctx,
                MindmapResolveInput(
                    map_key=key,
                    node_id=q_ids[i],
                    disposition="resolved",
                    resolution_summary=f"Branch {i}: went with A",
                    resolved_by_memory_id=long_pos.node_id,  # type: ignore[attr-defined]
                    resolved_by_party="owner",
                    team_id=team,
                ),
            )

        after = await MindmapGetTool()(ctx, MindmapGetInput(map_key=key, team_id=team))
        assert len(after.open_loops) == 1, "resolved questions must leave open_loops"  # type: ignore[attr-defined]
        assert after.open_loops == [q_ids[2]]  # type: ignore[attr-defined]

        # Settled branches come back as one-liners, not full nodes.
        summaries = {r.memory_id: r.resolution_summary for r in after.resolved}  # type: ignore[attr-defined]
        assert summaries[q_ids[0]] == "Branch 0: went with A"
        assert q_ids[0] not in {n.memory_id for n in after.nodes}  # type: ignore[attr-defined]

        # The resolves-under edge was written too, so the graph stays walkable.
        async with pg_pool.acquire() as conn:
            kind = await conn.fetchval(
                "SELECT reference_kind FROM memory_references "
                "WHERE source_memory_id = $1 AND target_memory_id = $2",
                long_pos.node_id,  # type: ignore[attr-defined]
                q_ids[0],
            )
        assert kind == "resolves-under"

        # --- delta read ------------------------------------------------------
        cursor = after.cursor  # type: ignore[attr-defined]
        nothing = await MindmapGetTool()(
            ctx, MindmapGetInput(map_key=key, team_id=team, since=cursor)
        )
        assert nothing.nodes == [], "a no-op resume must send no nodes at all"  # type: ignore[attr-defined]

        # The owner's reply hands the turn BACK: responsible_party is whose move
        # it is, not who wrote the node. Leaving it 'owner' would park the map in
        # the owner's own queue waiting on a move they had already made.
        await MindmapWriteNodeTool()(
            ctx,
            MindmapWriteNodeInput(
                map_key=key,
                content="Owner answers branch 2: go with B",
                node_role="note",
                responsible_party="model",
                team_id=team,
            ),
        )
        delta = await MindmapGetTool()(
            ctx, MindmapGetInput(map_key=key, team_id=team, since=cursor)
        )
        contents = [n.content for n in delta.nodes]  # type: ignore[attr-defined]
        assert any("go with B" in c for c in contents), contents
        assert len(delta.nodes) <= 2, "delta must not re-send the whole map"  # type: ignore[attr-defined]

        # --- the acceptance test: delta is dramatically cheaper --------------
        full_again = await MindmapGetTool()(
            ctx, MindmapGetInput(map_key=key, team_id=team, include="all", depth="full")
        )
        full_bytes = _size(full_again.model_dump())
        delta_bytes = _size(delta.model_dump())
        assert delta_bytes < full_bytes * 0.5, (
            f"delta {delta_bytes}B vs full {full_bytes}B — the feature exists to "
            "make resume cheap; if this ratio regresses the saving is gone and "
            "nothing else would fail"
        )

        # --- queue + list ----------------------------------------------------
        queue = await MindmapQueueTool()(ctx, MindmapQueueInput(team_id=team))
        assert len(queue.questions) == 1  # type: ignore[attr-defined]
        assert queue.questions[0].memory_id == q_ids[2]  # type: ignore[attr-defined]
        assert queue.questions[0].decision_criteria == "if A then X2; if B then Y2"  # type: ignore[attr-defined]

        listed = await MindmapListTool()(ctx, MindmapListInput(team_id=team, state="live"))
        mine = [m for m in listed.maps if m.map_key == key]  # type: ignore[attr-defined]
        assert len(mine) == 1
        assert mine[0].open_loop_count == 1

        only_open = await MindmapListTool()(
            ctx, MindmapListInput(team_id=team, state="live", has_open_loops=True)
        )
        assert key in {m.map_key for m in only_open.maps}  # type: ignore[attr-defined]

    finally:
        await _cleanup(pg_pool, tenant, team, client_id)


async def test_close_refuses_unread_owner_input(pg_pool: Any) -> None:
    """Graduation is irreversible, so it must fail closed on unread human input."""
    async with pg_pool.acquire() as conn, conn.transaction():
        s = await _setup(conn)
    tenant, team, client_id = s["tenant"], s["team"], s["client_id"]
    ctx = _ctx(pg_pool, tenant, client_id)

    try:
        opened = await MindmapOpenTool()(
            ctx,
            MindmapOpenInput(
                title="Guard e2e",
                root_question="Does close refuse unread owner input?",
                team_id=team,
            ),
        )
        key = opened.map_key  # type: ignore[attr-defined]

        # Agent reads, then the owner writes afterwards.
        await MindmapGetTool()(ctx, MindmapGetInput(map_key=key, team_id=team))
        await MindmapWriteNodeTool()(
            ctx,
            MindmapWriteNodeInput(
                map_key=key,
                content="Owner objection the agent has not read",
                node_role="challenge",
                responsible_party="model",
                authored_by="owner",
                team_id=team,
            ),
        )

        with pytest.raises(JsonRpcError) as exc:
            await MindmapCloseTool()(
                ctx, MindmapCloseInput(map_key=key, confirmed_decisions=[], team_id=team)
            )
        assert exc.value.data["code"] == "unacknowledged_owner_input"  # type: ignore[index]
        assert exc.value.data["nodes"]  # type: ignore[index]

        # After reading, graduation proceeds.
        await MindmapGetTool()(ctx, MindmapGetInput(map_key=key, team_id=team))
        closed = await MindmapCloseTool()(
            ctx, MindmapCloseInput(map_key=key, confirmed_decisions=[], team_id=team)
        )
        assert closed.state == "archived"  # type: ignore[attr-defined]

    finally:
        await _cleanup(pg_pool, tenant, team, client_id)


async def test_answer_resolves_and_guards_graduation(pg_pool: Any) -> None:
    """mindmap_answer end to end, plus the guard case turn-based logic missed.

    An owner ANSWERING hands the turn back to the model. If the guard keyed on
    turn it would see nothing owner-turn outstanding and happily graduate past
    the human's answer — so this asserts the opposite.
    """
    async with pg_pool.acquire() as conn, conn.transaction():
        s = await _setup(conn)
    tenant, team, client_id = s["tenant"], s["team"], s["client_id"]
    ctx = _ctx(pg_pool, tenant, client_id)

    try:
        opened = await MindmapOpenTool()(
            ctx,
            MindmapOpenInput(
                title="Answer e2e",
                root_question="Does answering settle the branch?",
                team_id=team,
            ),
        )
        key = opened.map_key  # type: ignore[attr-defined]

        q = await MindmapWriteNodeTool()(
            ctx,
            MindmapWriteNodeInput(
                map_key=key,
                content="Redis or Postgres for the hot path?",
                node_role="question",
                responsible_party="owner",
                decision_criteria="if Redis, size the cache; if Postgres, revisit indexes",
                team_id=team,
            ),
        )
        qid = q.node_id  # type: ignore[attr-defined]

        # The agent's own question must NOT look like unread owner input.
        await MindmapGetTool()(ctx, MindmapGetInput(map_key=key, team_id=team))
        queued = await MindmapQueueTool()(ctx, MindmapQueueInput(team_id=team))
        assert [x.memory_id for x in queued.questions] == [qid]  # type: ignore[attr-defined]

        answered = await MindmapAnswerTool()(
            ctx,
            MindmapAnswerInput(
                map_key=key,
                question_node_id=qid,
                content="Redis.\nThe write volume is well inside what it handles.",
                answered_by="owner",
                ratification_strength="explicit-endorse",
                ratification_citation="go with redis",
                team_id=team,
            ),
        )
        assert answered.question_status == "resolved"  # type: ignore[attr-defined]
        assert answered.resolution_summary == "Redis."  # type: ignore[attr-defined]

        # Answering retires it from the queue and from open loops.
        after = await MindmapGetTool()(ctx, MindmapGetInput(map_key=key, team_id=team))
        assert qid not in after.open_loops  # type: ignore[attr-defined]
        assert qid in {r.memory_id for r in after.resolved}  # type: ignore[attr-defined]

        empty = await MindmapQueueTool()(ctx, MindmapQueueInput(team_id=team))
        assert empty.questions == []  # type: ignore[attr-defined]

        # With the new mode parameter, answering a resolved question now succeeds
        # (mode="resolve" by default). A new answer node is created and linked,
        # and the question's resolved_by_memory_id points to the newest answer.
        re_answered = await MindmapAnswerTool()(
            ctx,
            MindmapAnswerInput(
                map_key=key,
                question_node_id=qid,
                content="Thought about it more: Postgres after all.",
                answered_by="owner",
                team_id=team,
            ),
        )
        assert re_answered.question_status == "resolved"  # type: ignore[attr-defined]

        # THE guard case. Note the reads above have already acknowledged the
        # answer — that is correct, so a fresh owner write is needed to arm the
        # guard. It set turn='model', so turn-based logic would see nothing
        # outstanding; authorship is what counts.
        await MindmapWriteNodeTool()(
            ctx,
            MindmapWriteNodeInput(
                map_key=key,
                content="One more thing before you settle this",
                node_role="challenge",
                responsible_party="model",
                authored_by="owner",
                team_id=team,
            ),
        )
        with pytest.raises(JsonRpcError) as exc:
            await MindmapCloseTool()(
                ctx, MindmapCloseInput(map_key=key, confirmed_decisions=[], team_id=team)
            )
        assert exc.value.data["code"] == "unacknowledged_owner_input"  # type: ignore[index]

        await MindmapGetTool()(ctx, MindmapGetInput(map_key=key, team_id=team))
        closed = await MindmapCloseTool()(
            ctx, MindmapCloseInput(map_key=key, confirmed_decisions=[], team_id=team)
        )
        assert closed.state == "archived"  # type: ignore[attr-defined]

    finally:
        await _cleanup(pg_pool, tenant, team, client_id)


async def test_turn_backfill_handles_double_encoded_metadata(pg_pool: Any) -> None:
    """The 0044 backfill matched nothing and said so to nobody.

    `memories.metadata` is a jsonb column holding a JSON-encoded STRING, not an
    object, so `metadata->>'key'` yields NULL instead of erroring. The UPDATE
    touched 0 of 208 rows on prod and reported success. This pins the decode for
    both shapes so the repair cannot silently rot back.
    """
    async with pg_pool.acquire() as conn:
        decode = """
            SELECT CASE
                WHEN jsonb_typeof($1::text::jsonb) = 'string'
                    THEN ($1::text::jsonb #>> ARRAY[]::text[])::jsonb ->> 'responsible_party'
                WHEN jsonb_typeof($1::text::jsonb) = 'object'
                    THEN $1::text::jsonb ->> 'responsible_party'
                ELSE NULL
            END
        """
        # Double-encoded: what the write path actually produces today.
        double = json.dumps(json.dumps({"node_role": "position", "responsible_party": "owner"}))
        assert await conn.fetchval(decode, double) == "owner"

        # Plain object: what it would produce if the write path were fixed.
        plain = json.dumps({"node_role": "position", "responsible_party": "model"})
        assert await conn.fetchval(decode, plain) == "model"

        # The naive 0044 form silently returns NULL on the real shape — this is
        # the assertion that would have caught it before deploy.
        naive = await conn.fetchval("SELECT $1::text::jsonb ->> 'responsible_party'", double)
        assert naive is None

        # Absent key stays NULL rather than erroring.
        empty = json.dumps(json.dumps({"node_role": "note"}))
        assert await conn.fetchval(decode, empty) is None


async def test_answer_modes_reopen_comment_and_re_resolve(pg_pool: Any) -> None:
    """Answering is the caller's prerogative, not the tool's.

    A resolved question can be answered again — resolve refreshes it, comment
    leaves it settled while accruing a note, reopen puts it back in play. Every
    answer is a NEW node; none overwrites a prior one. This locks the removal of
    the old `question_not_open` gate.
    """
    async with pg_pool.acquire() as conn, conn.transaction():
        s = await _setup(conn)
    tenant, team, client_id = s["tenant"], s["team"], s["client_id"]
    ctx = _ctx(pg_pool, tenant, client_id)

    async def _status(qid: UUID) -> dict[str, Any]:
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, turn, resolved_at, resolved_by_memory_id, resolution_summary "
                "FROM memory_map_membership WHERE memory_id = $1",
                qid,
            )
        return dict(row)

    async def _answer_count(qid: UUID) -> int:
        async with pg_pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT count(*) FROM memory_references "
                "WHERE target_memory_id = $1 AND reference_kind = 'answers'",
                qid,
            )

    try:
        opened = await MindmapOpenTool()(
            ctx,
            MindmapOpenInput(
                title="Answer modes e2e",
                root_question="Can a settled question be answered again?",
                team_id=team,
            ),
        )
        key = opened.map_key  # type: ignore[attr-defined]

        q = await MindmapWriteNodeTool()(
            ctx,
            MindmapWriteNodeInput(
                map_key=key,
                content="Which datastore for the queue?",
                node_role="question",
                responsible_party="owner",
                team_id=team,
            ),
        )
        qid = q.node_id  # type: ignore[attr-defined]

        # --- first answer, default mode → resolve ---------------------------
        a1 = await MindmapAnswerTool()(
            ctx,
            MindmapAnswerInput(
                map_key=key,
                question_node_id=qid,
                content="Postgres — we already run it.",
                answered_by="owner",
                team_id=team,
            ),
        )
        st = await _status(qid)
        assert st["status"] == "resolved"
        assert st["resolved_by_memory_id"] == a1.answer_node_id  # type: ignore[attr-defined]
        assert st["resolved_at"] is not None

        # --- answer an ALREADY-resolved question: no question_not_open error -
        # mode=resolve refreshes the pointer to the newest answer.
        a2 = await MindmapAnswerTool()(
            ctx,
            MindmapAnswerInput(
                map_key=key,
                question_node_id=qid,
                content="On reflection, Postgres with SKIP LOCKED.",
                answered_by="owner",
                mode="resolve",
                team_id=team,
            ),
        )
        st = await _status(qid)
        assert st["status"] == "resolved"
        assert st["resolved_by_memory_id"] == a2.answer_node_id  # type: ignore[attr-defined]
        assert await _answer_count(qid) == 2, "prior answer node must survive"

        # --- comment on a resolved question: status untouched ---------------
        await MindmapAnswerTool()(
            ctx,
            MindmapAnswerInput(
                map_key=key,
                question_node_id=qid,
                content="Note: SKIP LOCKED needs a stable ORDER BY.",
                answered_by="owner",
                mode="comment",
                team_id=team,
            ),
        )
        st = await _status(qid)
        assert st["status"] == "resolved", "comment must not disturb resolution"
        assert st["resolved_by_memory_id"] == a2.answer_node_id  # type: ignore[attr-defined]
        assert await _answer_count(qid) == 3

        # --- reopen a resolved question -------------------------------------
        await MindmapAnswerTool()(
            ctx,
            MindmapAnswerInput(
                map_key=key,
                question_node_id=qid,
                content="Actually reconsidering — Redis streams may fit better.",
                answered_by="owner",
                mode="reopen",
                team_id=team,
            ),
        )
        st = await _status(qid)
        assert st["status"] == "open"
        assert st["resolved_at"] is None
        assert st["resolved_by_memory_id"] is None
        assert st["turn"] == "model", "owner reopened → ball is with the model"
        assert await _answer_count(qid) == 4

        # Reopen surfaces the question as a live, unresolved node again — it
        # leaves `resolved` and comes back in `nodes` with status 'open'. It is
        # NOT in open_loops: the owner answered, so the turn is the model's and
        # open_loops is owner-facing (turn='owner' AND status='open').
        after = await MindmapGetTool()(ctx, MindmapGetInput(map_key=key, team_id=team))
        assert qid not in {r.memory_id for r in after.resolved}  # type: ignore[attr-defined]
        reopened = next(n for n in after.nodes if n.memory_id == qid)  # type: ignore[attr-defined]
        assert reopened.status == "open"
        assert qid not in set(after.open_loops)  # type: ignore[attr-defined]

        # --- legacy auto_resolve=False still means reopen -------------------
        # First settle it again, then answer with the deprecated boolean.
        await MindmapAnswerTool()(
            ctx,
            MindmapAnswerInput(
                map_key=key,
                question_node_id=qid,
                content="Settle: Redis streams.",
                answered_by="owner",
                mode="resolve",
                team_id=team,
            ),
        )
        assert (await _status(qid))["status"] == "resolved"
        await MindmapAnswerTool()(
            ctx,
            MindmapAnswerInput(
                map_key=key,
                question_node_id=qid,
                content="Legacy reopen path.",
                answered_by="owner",
                auto_resolve=False,
                team_id=team,
            ),
        )
        assert (await _status(qid))["status"] == "open"

    finally:
        await _cleanup(pg_pool, tenant, team, client_id)
