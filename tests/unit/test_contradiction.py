"""Tests for contradiction candidates on memory_write (issue #315).

No LLM in the pipeline: the server returns a deterministic recency window
of same-type memories; the calling model judges contradictions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mem_mcp.audit.logger import NoopAuditLogger
from mem_mcp.embeddings.bedrock import EmbedResult
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._deps import NoopQuotas, ToolDeps
from mem_mcp.mcp.tools.write import MemoryWriteInput, MemoryWriteOutput, MemoryWriteTool
from mem_mcp.memory.contradiction import (
    SNIPPET_MAX_CHARS,
    ContradictionCandidate,
    find_recent_candidates,
)

# --------------------------------------------------------------------------
# find_recent_candidates
# --------------------------------------------------------------------------


class _FakeFetchConn:
    """Fake asyncpg.Connection scripting .fetch returns."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append((query, args))
        return self.rows


def _row(content: str, type_: str = "note") -> dict[str, Any]:
    return {
        "id": uuid4(),
        "content": content,
        "type": type_,
        "tags": ["t1"],
        "created_at": datetime.now(tz=UTC),
    }


class TestFindRecentCandidates:
    @pytest.mark.asyncio
    async def test_maps_rows_to_candidates(self) -> None:
        rows = [_row("we use MySQL"), _row("we ship monthly")]
        conn = _FakeFetchConn(rows)
        out = await find_recent_candidates(conn, uuid4(), "note", limit=3)
        assert [c.memory_id for c in out] == [r["id"] for r in rows]
        assert out[0].content_snippet == "we use MySQL"
        assert out[0].type == "note"
        assert out[0].tags == ["t1"]

    @pytest.mark.asyncio
    async def test_empty_window_returns_empty(self) -> None:
        conn = _FakeFetchConn([])
        out = await find_recent_candidates(conn, uuid4(), "note", limit=3)
        assert out == []

    @pytest.mark.asyncio
    async def test_query_params_and_scoping(self) -> None:
        conn = _FakeFetchConn([])
        tenant = uuid4()
        await find_recent_candidates(conn, tenant, "decision", limit=7)
        query, args = conn.calls[0]
        assert args == (tenant, "decision", 7)
        assert "ORDER BY created_at DESC" in query
        assert "deleted_at IS NULL" in query
        assert "is_current = true" in query

    @pytest.mark.asyncio
    async def test_snippet_truncated(self) -> None:
        long_content = "y" * 500
        conn = _FakeFetchConn([_row(long_content)])
        out = await find_recent_candidates(conn, uuid4(), "note", limit=1)
        assert len(out[0].content_snippet) == SNIPPET_MAX_CHARS

    @pytest.mark.asyncio
    async def test_null_tags_tolerated(self) -> None:
        row = _row("x")
        row["tags"] = None
        conn = _FakeFetchConn([row])
        out = await find_recent_candidates(conn, uuid4(), "note", limit=1)
        assert out[0].tags == []


# --------------------------------------------------------------------------
# MemoryWriteInput validation
# --------------------------------------------------------------------------


class TestWriteInputValidation:
    def test_defaults(self) -> None:
        m = MemoryWriteInput.model_validate({"content": "x"})
        assert m.check_contradictions is False
        assert m.contradiction_limit == 3

    @pytest.mark.parametrize("bad", [0, 11, -1])
    def test_contradiction_limit_out_of_range(self, bad: int) -> None:
        with pytest.raises(ValidationError):
            MemoryWriteInput.model_validate({"content": "x", "contradiction_limit": bad})

    @pytest.mark.parametrize("ok", [1, 10])
    def test_contradiction_limit_bounds_accepted(self, ok: int) -> None:
        m = MemoryWriteInput.model_validate({"content": "x", "contradiction_limit": ok})
        assert m.contradiction_limit == ok


# --------------------------------------------------------------------------
# MemoryWriteTool wiring (fake conn — same pattern as test_tools.py)
# --------------------------------------------------------------------------


class FakeEmbeddings:
    async def embed(self, text: str) -> EmbedResult:
        return EmbedResult(vector=[0.1] * 1024, input_tokens=12)


def _build_ctx() -> ToolContext:
    return ToolContext(
        request_id=str(uuid4()),
        tenant_id=uuid4(),
        identity_id=uuid4(),
        client_id="client-1",
        scopes=frozenset(("memory.read", "memory.write")),
        db_pool=MagicMock(),
        deps=ToolDeps(
            embeddings=FakeEmbeddings(),
            audit=NoopAuditLogger(),
            quotas=NoopQuotas(),
        ),
    )


def _patch_tenant_tx(monkeypatch: pytest.MonkeyPatch, conn: Any) -> None:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_tx(pool: Any, tenant_id: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.mcp.tools.write.tenant_tx", fake_tx)


def _insert_conn(candidate_rows: list[dict[str, Any]] | None = None) -> AsyncMock:
    """Conn scripted for the no-dedupe plain-INSERT path.

    fetch (the candidate query) returns candidate_rows; fetchrow serves
    check_dup (hash, cosine) then the final INSERT.
    """
    conn = AsyncMock()
    conn.fetch.return_value = candidate_rows or []
    conn.fetchrow.side_effect = [
        None,  # hash check (in check_dup)
        None,  # cosine check (in check_dup)
        {"id": uuid4(), "version": 1, "created_at": datetime.now(tz=UTC)},  # INSERT
    ]
    return conn


class TestMemoryWriteContradictionCandidates:
    @pytest.mark.asyncio
    async def test_default_off_no_fetch_and_null_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """check_contradictions=false → no candidate query, field None."""
        conn = _insert_conn()
        _patch_tenant_tx(monkeypatch, conn)
        result = await MemoryWriteTool()(_build_ctx(), MemoryWriteInput(content="x"))
        assert isinstance(result, MemoryWriteOutput)
        assert result.contradiction_candidates is None
        conn.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_candidates_returned_and_write_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [_row("we use MySQL")]
        conn = _insert_conn(candidate_rows=rows)
        _patch_tenant_tx(monkeypatch, conn)
        result = await MemoryWriteTool()(
            _build_ctx(),
            MemoryWriteInput(content="we use PostgreSQL", check_contradictions=True),
        )
        assert isinstance(result, MemoryWriteOutput)
        assert result.id is not None
        assert result.deduped is False
        assert result.contradiction_candidates is not None
        assert [c.memory_id for c in result.contradiction_candidates] == [rows[0]["id"]]

    @pytest.mark.asyncio
    async def test_empty_window_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _insert_conn(candidate_rows=[])
        _patch_tenant_tx(monkeypatch, conn)
        result = await MemoryWriteTool()(
            _build_ctx(), MemoryWriteInput(content="x", check_contradictions=True)
        )
        assert isinstance(result, MemoryWriteOutput)
        assert result.contradiction_candidates == []

    @pytest.mark.asyncio
    async def test_contradiction_limit_passed_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = _insert_conn()
        _patch_tenant_tx(monkeypatch, conn)
        await MemoryWriteTool()(
            _build_ctx(),
            MemoryWriteInput(content="x", check_contradictions=True, contradiction_limit=7),
        )
        # fetch(query, tenant_id, type_, limit)
        assert conn.fetch.call_args[0][3] == 7

    @pytest.mark.asyncio
    async def test_dedupe_early_return_skips_fetch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deduped write returns before the candidate fetch runs."""
        existing = uuid4()
        conn = AsyncMock()
        conn.fetch.return_value = [_row("should never be fetched")]
        conn.fetchrow.side_effect = [
            {"id": existing},  # hash check hits
            {"id": existing, "version": 2, "created_at": datetime.now(tz=UTC)},  # merge UPDATE
        ]
        _patch_tenant_tx(monkeypatch, conn)
        result = await MemoryWriteTool()(
            _build_ctx(),
            MemoryWriteInput(content="x", check_contradictions=True),
        )
        assert isinstance(result, MemoryWriteOutput)
        assert result.deduped is True
        conn.fetch.assert_not_called()
        assert result.contradiction_candidates == []  # stable shape: asked-but-skipped

    @pytest.mark.asyncio
    async def test_output_serializes_candidates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """contradiction_candidates round-trips through pydantic JSON serialization."""
        rows = [_row("we use MySQL")]
        conn = _insert_conn(candidate_rows=rows)
        _patch_tenant_tx(monkeypatch, conn)
        result = await MemoryWriteTool()(
            _build_ctx(),
            MemoryWriteInput(content="x", check_contradictions=True),
        )
        assert isinstance(result, MemoryWriteOutput)
        dumped = result.model_dump(mode="json")
        assert dumped["contradiction_candidates"][0]["memory_id"] == str(rows[0]["id"])
        assert dumped["contradiction_candidates"][0]["content_snippet"] == "we use MySQL"

    def test_candidate_has_no_judgment_field(self) -> None:
        """The substrate never claims a contradiction — no score on the shape."""
        fields = {f.name for f in ContradictionCandidate.__dataclass_fields__.values()}
        assert "contradiction_score" not in fields
        assert fields == {"memory_id", "content_snippet", "type", "tags", "created_at"}
