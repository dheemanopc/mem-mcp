"""Branch status, delta reads, and the open-loop fix.

These exist because walking a map turn-by-turn kept every earlier branch's full
discussion resident in context while later branches were worked, so cost grew
with total discussion volume rather than with open work. The behaviours asserted
here are the ones that make a long map cheap to resume; if they regress, the
symptom is a larger bill rather than a failing request, which is exactly the
kind of regression nobody notices.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from mem_mcp.mcp.tools.mindmap_get import (
    SUMMARY_CHARS,
    MindmapGetInput,
    _is_truncated,
    _maybe_truncate,
)

ROOT = UUID("498efbde-237a-4af1-9d54-fe696a7f71c9")


# --------------------------------------------------------------------------
# Content compaction
# --------------------------------------------------------------------------


def test_summary_depth_truncates_long_content() -> None:
    long = "x" * (SUMMARY_CHARS + 500)
    out = _maybe_truncate(long, "summary")
    assert len(out) <= SUMMARY_CHARS + 1
    assert out.endswith("…")


def test_full_depth_returns_content_whole() -> None:
    long = "x" * (SUMMARY_CHARS + 500)
    assert _maybe_truncate(long, "full") == long


def test_short_content_is_never_marked_truncated() -> None:
    short = "brief node"
    assert _maybe_truncate(short, "summary") == short
    assert _is_truncated(short, "summary") is False


def test_truncated_flag_tracks_actual_truncation() -> None:
    """The flag must not claim truncation that did not happen — a caller uses it
    to decide whether a `depth="full"` refetch is worth the tokens."""
    assert _is_truncated("x" * (SUMMARY_CHARS + 1), "summary") is True
    assert _is_truncated("x" * SUMMARY_CHARS, "summary") is False
    assert _is_truncated("x" * (SUMMARY_CHARS + 1), "full") is False


# --------------------------------------------------------------------------
# Input defaults — the cheap path must be the default path
# --------------------------------------------------------------------------


def test_get_defaults_to_the_cheap_read() -> None:
    """If the defaults were include='all'/depth='full', every resume would pay
    full price and the feature would silently do nothing."""
    inp = MindmapGetInput(map_key="m")
    assert inp.include == "open"
    assert inp.depth == "summary"
    assert inp.since is None


def test_get_rejects_negative_cursor() -> None:
    with pytest.raises(ValueError):
        MindmapGetInput(map_key="m", since=-1)


# --------------------------------------------------------------------------
# fetch_members filtering
# --------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.queries.append((sql, args))
        return self.rows

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.queries.append((sql, args))
        return 0

    async def execute(self, sql: str, *args: Any) -> str:
        self.queries.append((sql, args))
        return "UPDATE 1"


@pytest.mark.asyncio
async def test_empty_since_list_returns_nothing_not_everything() -> None:
    """THE dangerous edge: 'no nodes changed' must not fall through to 'no filter'.

    If an empty id list were treated as absent, a no-op resume would return the
    ENTIRE map — the precise bloat this feature exists to remove, and it would
    look like success.
    """
    from mem_mcp.mindmap import service

    conn = _FakeConn(rows=[{"memory_id": uuid4()}])
    out = await service.fetch_members(
        conn,
        root_memory_id=ROOT,
        tenant_id=uuid4(),
        since_memory_ids=[],
    )
    assert out == []
    assert conn.queries == [], "must not query at all when nothing changed"


@pytest.mark.asyncio
async def test_status_filter_is_applied_to_the_query() -> None:
    from mem_mcp.mindmap import service

    conn = _FakeConn()
    await service.fetch_members(
        conn,
        root_memory_id=ROOT,
        tenant_id=uuid4(),
        statuses=["open"],
    )
    sql, args = conn.queries[0]
    assert "mm.status = ANY" in sql
    assert ["open"] in args


@pytest.mark.asyncio
async def test_no_status_filter_returns_all_branches() -> None:
    from mem_mcp.mindmap import service

    conn = _FakeConn()
    await service.fetch_members(
        conn,
        root_memory_id=ROOT,
        tenant_id=uuid4(),
        statuses=None,
    )
    sql, _ = conn.queries[0]
    assert "mm.status = ANY" not in sql


# --------------------------------------------------------------------------
# Resolve
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_node_status_is_scoped_by_map() -> None:
    """A caller must not be able to resolve a node in a different map by id."""
    from mem_mcp.mindmap import service

    conn = _FakeConn()
    await service.set_node_status(
        conn,
        memory_id=uuid4(),
        root_memory_id=ROOT,
        status="resolved",
        resolution_summary="settled",
        resolved_by_memory_id=None,
        resolved_by_party="owner",
    )
    sql, _ = conn.queries[0]
    assert "memory_id = $1 AND root_memory_id = $2" in sql


@pytest.mark.asyncio
async def test_set_node_status_reports_miss() -> None:
    """UPDATE 0 means the node was not in this map — the tool must 400, not
    silently claim success."""
    from mem_mcp.mindmap import service

    class _MissConn(_FakeConn):
        async def execute(self, sql: str, *args: Any) -> str:
            return "UPDATE 0"

    ok = await service.set_node_status(
        _MissConn(),
        memory_id=uuid4(),
        root_memory_id=ROOT,
        status="resolved",
        resolution_summary="s",
        resolved_by_memory_id=None,
        resolved_by_party="model",
    )
    assert ok is False


def test_resolution_summary_is_mandatory() -> None:
    """A branch that cannot be summarised in a line is not settled — and this
    string is what every later cheap read shows in place of the branch."""
    from mem_mcp.mcp.tools.mindmap_resolve import MindmapResolveInput

    with pytest.raises(ValueError):
        MindmapResolveInput(map_key="m", node_id=uuid4(), resolution_summary="")


def test_resolve_disposition_is_constrained() -> None:
    from mem_mcp.mcp.tools.mindmap_resolve import MindmapResolveInput

    with pytest.raises(ValueError):
        MindmapResolveInput(
            map_key="m",
            node_id=uuid4(),
            resolution_summary="s",
            disposition="closed",  # type: ignore[arg-type]
        )


# --------------------------------------------------------------------------
# Question nodes
# --------------------------------------------------------------------------


def test_write_node_accepts_question_role_and_criteria() -> None:
    from mem_mcp.mcp.tools.mindmap_write_node import MindmapWriteNodeInput

    inp = MindmapWriteNodeInput(
        map_key="m",
        content="Redis or Postgres for the hot path?",
        node_role="question",
        responsible_party="owner",
        decision_criteria="if Redis, I size the cache; if Postgres, I revisit indexes",
    )
    assert inp.node_role == "question"
    assert inp.decision_criteria is not None


def test_decision_criteria_defaults_to_none_for_ordinary_nodes() -> None:
    from mem_mcp.mcp.tools.mindmap_write_node import MindmapWriteNodeInput

    inp = MindmapWriteNodeInput(map_key="m", content="a position")
    assert inp.decision_criteria is None
