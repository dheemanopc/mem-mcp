"""Web surface over the mindmap tools — auth, routing, and error translation.

The router deliberately holds no lifecycle logic: it resolves a session into a
ToolContext and delegates to the same MCP tools. These tests pin that contract,
because the moment the web path grows its own rules it will drift from the MCP
path — which is exactly how the two provisioning paths diverged and left two
users unable to log in.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.web.handlers.mindmaps import make_mindmaps_router

_MOD = "mem_mcp.web.handlers.mindmaps"
TENANT = uuid4()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(make_mindmaps_router(pool=MagicMock(), deps=MagicMock()))
    return TestClient(app)


def _session() -> Any:
    sess = MagicMock()
    sess.tenant_id = TENANT
    sess.identity_id = TENANT
    return sess


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/web/mindmaps"),
        ("get", "/api/web/mindmaps/queue"),
        ("get", "/api/web/mindmaps/some-map"),
        ("post", "/api/web/mindmaps/some-map/answer"),
        ("post", "/api/web/mindmaps/some-map/resolve"),
    ],
)
def test_every_endpoint_requires_a_session(method: str, path: str) -> None:
    """No endpoint may be reachable without a session cookie."""
    client = _client()
    # POST bodies must be VALID here: FastAPI validates the body before the
    # handler runs, so an invalid one returns 422 and would mask whether auth is
    # enforced at all. (That ordering is framework behaviour and leaks nothing —
    # no handler runs and no data is returned either way.)
    bodies = {
        "/api/web/mindmaps/some-map/answer": {
            "question_node_id": str(uuid4()),
            "content": "x",
        },
        "/api/web/mindmaps/some-map/resolve": {
            "node_id": str(uuid4()),
            "resolution_summary": "x",
        },
    }
    kwargs: dict[str, Any] = {"json": bodies[path]} if method == "post" else {}
    res = getattr(client, method)(path, **kwargs)
    assert res.status_code == 401, f"{method.upper()} {path} did not require auth"


def test_invalid_session_is_rejected() -> None:
    client = _client()
    with patch(f"{_MOD}.lookup_session", AsyncMock(return_value=None)):
        res = client.get("/api/web/mindmaps", cookies={"mem_session": "bogus"})
    assert res.status_code == 401


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def test_queue_is_not_swallowed_as_a_map_key() -> None:
    """/mindmaps/queue must hit the queue handler, not get_map("queue").

    Route order is load-bearing here: FastAPI matches in declaration order, so
    a /{map_key} route declared first would capture "queue" silently and the
    inbox would 404 as a missing map.
    """
    client = _client()
    queue_out = MagicMock()
    queue_out.model_dump.return_value = {"questions": [], "request_id": "r"}

    with (
        patch(f"{_MOD}.lookup_session", AsyncMock(return_value=_session())),
        patch(f"{_MOD}.MindmapQueueTool") as queue_tool,
        patch(f"{_MOD}.MindmapGetTool") as get_tool,
    ):
        queue_tool.return_value = AsyncMock(return_value=queue_out)
        res = client.get("/api/web/mindmaps/queue", cookies={"mem_session": "s"})

    assert res.status_code == 200
    assert res.json() == {"questions": [], "request_id": "r"}
    get_tool.assert_not_called()


def test_get_map_defaults_to_the_full_read() -> None:
    """Agent defaults are cheap (open/summary); a human reading one map wants it
    whole, and no agent context is being spent."""
    client = _client()
    out = MagicMock()
    out.model_dump.return_value = {"map_key": "m"}
    captured: dict[str, Any] = {}

    async def fake_call(ctx: Any, inp: Any) -> Any:
        captured["include"] = inp.include
        captured["depth"] = inp.depth
        return out

    with (
        patch(f"{_MOD}.lookup_session", AsyncMock(return_value=_session())),
        patch(f"{_MOD}.MindmapGetTool") as get_tool,
    ):
        get_tool.return_value = fake_call
        res = client.get("/api/web/mindmaps/some-map", cookies={"mem_session": "s"})

    assert res.status_code == 200
    assert captured == {"include": "all", "depth": "full"}


# --------------------------------------------------------------------------
# Answering
# --------------------------------------------------------------------------


def test_answer_marks_the_human_as_author() -> None:
    """answered_by='owner' is what lets the graduation guard refuse to settle a
    map past this input. Defaulting it to 'model' would silently disarm that."""
    client = _client()
    out = MagicMock()
    out.model_dump.return_value = {"question_status": "resolved"}
    captured: dict[str, Any] = {}

    async def fake_call(ctx: Any, inp: Any) -> Any:
        captured["answered_by"] = inp.answered_by
        captured["citation"] = inp.ratification_citation
        return out

    with (
        patch(f"{_MOD}.lookup_session", AsyncMock(return_value=_session())),
        patch(f"{_MOD}.MindmapAnswerTool") as tool,
    ):
        tool.return_value = fake_call
        res = client.post(
            "/api/web/mindmaps/some-map/answer",
            cookies={"mem_session": "s"},
            json={"question_node_id": str(uuid4()), "content": "go with B"},
        )

    assert res.status_code == 200
    assert captured["answered_by"] == "owner"
    # No endorsement requested -> no citation invented on the human's behalf.
    assert captured["citation"] is None


def test_endorsement_cites_the_humans_own_words() -> None:
    client = _client()
    out = MagicMock()
    out.model_dump.return_value = {}
    captured: dict[str, Any] = {}

    async def fake_call(ctx: Any, inp: Any) -> Any:
        captured["citation"] = inp.ratification_citation
        captured["strength"] = inp.ratification_strength
        return out

    with (
        patch(f"{_MOD}.lookup_session", AsyncMock(return_value=_session())),
        patch(f"{_MOD}.MindmapAnswerTool") as tool,
    ):
        tool.return_value = fake_call
        client.post(
            "/api/web/mindmaps/some-map/answer",
            cookies={"mem_session": "s"},
            json={
                "question_node_id": str(uuid4()),
                "content": "Redis, the write volume is fine",
                "ratification_strength": "explicit-endorse",
            },
        )

    assert captured["strength"] == "explicit-endorse"
    assert captured["citation"] == "Redis, the write volume is fine"


def test_answer_requires_content() -> None:
    client = _client()
    with patch(f"{_MOD}.lookup_session", AsyncMock(return_value=_session())):
        res = client.post(
            "/api/web/mindmaps/some-map/answer",
            cookies={"mem_session": "s"},
            json={"question_node_id": str(uuid4()), "content": ""},
        )
    assert res.status_code == 422


# --------------------------------------------------------------------------
# Error translation
# --------------------------------------------------------------------------


def test_lifecycle_refusals_reach_the_ui_with_their_code() -> None:
    """ "Already resolved" and "map archived" must be distinguishable in the UI.

    Flattening them to a bare 500 would leave the page unable to say anything
    more useful than "something went wrong".
    """
    client = _client()

    async def fake_call(ctx: Any, inp: Any) -> Any:
        raise JsonRpcError(
            -32602, "question is already resolved", data={"code": "question_not_open"}
        )

    with (
        patch(f"{_MOD}.lookup_session", AsyncMock(return_value=_session())),
        patch(f"{_MOD}.MindmapAnswerTool") as tool,
    ):
        tool.return_value = fake_call
        res = client.post(
            "/api/web/mindmaps/some-map/answer",
            cookies={"mem_session": "s"},
            json={"question_node_id": str(uuid4()), "content": "x"},
        )

    assert res.status_code == 400
    body = res.json()["detail"]
    assert body["code"] == "question_not_open"
    assert "already resolved" in body["message"]


def test_resolve_requires_a_summary() -> None:
    """A branch that cannot be summarised in a line is not settled — and this
    string is what every later cheap read shows in its place."""
    client = _client()
    with patch(f"{_MOD}.lookup_session", AsyncMock(return_value=_session())):
        res = client.post(
            "/api/web/mindmaps/some-map/resolve",
            cookies={"mem_session": "s"},
            json={"node_id": str(uuid4()), "resolution_summary": ""},
        )
    assert res.status_code == 422
