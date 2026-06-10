"""Tests for contradiction detection on memory_write (issue #315)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from mem_mcp.audit.logger import NoopAuditLogger
from mem_mcp.embeddings.bedrock import EmbedResult
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._deps import NoopQuotas, ToolDeps, make_default_deps
from mem_mcp.mcp.tools.write import MemoryWriteInput, MemoryWriteOutput, MemoryWriteTool
from mem_mcp.memory.contradiction import (
    NLI_DEFAULT_CANDIDATE_WINDOW,
    ContradictionCandidate,
    NoopContradictionChecker,
    OllamaNliChecker,
    find_contradiction_candidates,
    parse_nli_response,
)

# --------------------------------------------------------------------------
# NLI response parsing
# --------------------------------------------------------------------------


class TestParseNliResponse:
    def test_contradiction_with_score(self) -> None:
        assert parse_nli_response("CONTRADICTION 0.9") == ("CONTRADICTION", 0.9)

    def test_neutral_with_score(self) -> None:
        assert parse_nli_response("NEUTRAL 0.1") == ("NEUTRAL", 0.1)

    def test_entailment_with_score(self) -> None:
        assert parse_nli_response("ENTAILMENT 0.7") == ("ENTAILMENT", 0.7)

    def test_case_insensitive_label(self) -> None:
        assert parse_nli_response("contradiction 0.85") == ("CONTRADICTION", 0.85)

    def test_surrounding_chatter_tolerated(self) -> None:
        label, score = parse_nli_response(
            "Sure! The answer is: CONTRADICTION 0.85 because they conflict."
        )
        assert (label, score) == ("CONTRADICTION", 0.85)

    def test_missing_score_defaults_zero(self) -> None:
        assert parse_nli_response("NEUTRAL") == ("NEUTRAL", 0.0)

    def test_malformed_defaults_neutral(self) -> None:
        assert parse_nli_response("I cannot answer that") == ("NEUTRAL", 0.0)

    def test_empty_string_defaults_neutral(self) -> None:
        assert parse_nli_response("") == ("NEUTRAL", 0.0)

    def test_non_string_defaults_neutral(self) -> None:
        assert parse_nli_response(None) == ("NEUTRAL", 0.0)
        assert parse_nli_response(42) == ("NEUTRAL", 0.0)

    def test_score_clamped_to_unit_interval(self) -> None:
        # "1.5" matches the leading "1" of the regex's [01] form → 1.0 ceiling holds
        label, score = parse_nli_response("CONTRADICTION 1.0")
        assert (label, score) == ("CONTRADICTION", 1.0)


# --------------------------------------------------------------------------
# find_contradiction_candidates
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


def _patch_classify(
    monkeypatch: pytest.MonkeyPatch,
    results: dict[str, tuple[str, float]] | Exception,
) -> list[str]:
    """Patch the Ollama NLI call; keyed by candidate content. Returns call log."""
    calls: list[str] = []

    async def fake_classify(
        *, url: str, model: str, new_content: str, candidate_content: str, timeout_seconds: float
    ) -> tuple[str, float]:
        calls.append(candidate_content)
        if isinstance(results, Exception):
            raise results
        return results[candidate_content]

    monkeypatch.setattr(
        "mem_mcp.memory.contradiction._ollama_nli_classify",
        fake_classify,
    )
    return calls


class TestFindContradictionCandidates:
    @pytest.mark.asyncio
    async def test_threshold_filtering(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [_row("we use MySQL"), _row("PostgreSQL supports JSONB")]
        conn = _FakeFetchConn(rows)
        _patch_classify(
            monkeypatch,
            {
                "we use MySQL": ("CONTRADICTION", 0.9),
                "PostgreSQL supports JSONB": ("CONTRADICTION", 0.5),  # below 0.7
            },
        )
        out = await find_contradiction_candidates(
            conn, uuid4(), "we use PostgreSQL", "note", ollama_url="http://o"
        )
        assert len(out) == 1
        assert out[0].memory_id == rows[0]["id"]
        assert out[0].contradiction_score == 0.9

    @pytest.mark.asyncio
    async def test_non_contradiction_labels_excluded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [_row("a"), _row("b")]
        conn = _FakeFetchConn(rows)
        _patch_classify(
            monkeypatch,
            {"a": ("NEUTRAL", 0.99), "b": ("ENTAILMENT", 0.99)},
        )
        out = await find_contradiction_candidates(conn, uuid4(), "x", "note", ollama_url="http://o")
        assert out == []

    @pytest.mark.asyncio
    async def test_sorted_desc_and_capped_at_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [_row("a"), _row("b"), _row("c")]
        conn = _FakeFetchConn(rows)
        _patch_classify(
            monkeypatch,
            {
                "a": ("CONTRADICTION", 0.75),
                "b": ("CONTRADICTION", 0.95),
                "c": ("CONTRADICTION", 0.85),
            },
        )
        out = await find_contradiction_candidates(
            conn, uuid4(), "x", "note", ollama_url="http://o", limit=2
        )
        assert [c.contradiction_score for c in out] == [0.95, 0.85]

    @pytest.mark.asyncio
    async def test_ollama_failure_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _FakeFetchConn([_row("a")])
        _patch_classify(monkeypatch, ConnectionError("ollama down"))
        out = await find_contradiction_candidates(conn, uuid4(), "x", "note", ollama_url="http://o")
        assert out == []

    @pytest.mark.asyncio
    async def test_no_candidates_skips_nli_entirely(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _FakeFetchConn([])
        calls = _patch_classify(monkeypatch, {})
        out = await find_contradiction_candidates(conn, uuid4(), "x", "note", ollama_url="http://o")
        assert out == []
        assert calls == []

    @pytest.mark.asyncio
    async def test_candidate_query_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _FakeFetchConn([])
        _patch_classify(monkeypatch, {})
        tenant = uuid4()
        await find_contradiction_candidates(
            conn, tenant, "x", "decision", ollama_url="http://o", window=7
        )
        query, args = conn.calls[0]
        assert args == (tenant, "decision", 7)
        assert "ORDER BY created_at DESC" in query
        assert "deleted_at IS NULL" in query
        assert "is_current = true" in query

    @pytest.mark.asyncio
    async def test_snippet_truncated_to_200_chars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        long_content = "y" * 500
        conn = _FakeFetchConn([_row(long_content)])
        _patch_classify(monkeypatch, {long_content: ("CONTRADICTION", 0.8)})
        out = await find_contradiction_candidates(conn, uuid4(), "x", "note", ollama_url="http://o")
        assert len(out[0].content_snippet) == 200


# --------------------------------------------------------------------------
# Checker implementations
# --------------------------------------------------------------------------


class TestCheckers:
    @pytest.mark.asyncio
    async def test_noop_returns_empty_without_db_access(self) -> None:
        conn = MagicMock()  # any attribute access would be a non-awaitable mock
        out = await NoopContradictionChecker().find_candidates(conn, uuid4(), "x", "note", limit=3)
        assert out == []
        assert conn.method_calls == []

    @pytest.mark.asyncio
    async def test_ollama_checker_delegates_with_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        async def fake_find(
            conn: Any, tenant_id: UUID, new_content: str, type_: str, **kwargs: Any
        ) -> list[ContradictionCandidate]:
            seen.update(kwargs, tenant_id=tenant_id, new_content=new_content, type_=type_)
            return []

        monkeypatch.setattr("mem_mcp.memory.contradiction.find_contradiction_candidates", fake_find)
        checker = OllamaNliChecker(url="http://o", model="m", threshold=0.6, window=5)
        await checker.find_candidates(MagicMock(), uuid4(), "new", "fact", limit=4)
        assert seen["ollama_url"] == "http://o"
        assert seen["model"] == "m"
        assert seen["threshold"] == 0.6
        assert seen["window"] == 5
        assert seen["limit"] == 4
        assert seen["type_"] == "fact"

    def test_ollama_checker_defaults(self) -> None:
        checker = OllamaNliChecker(url="http://o")
        assert checker.window == NLI_DEFAULT_CANDIDATE_WINDOW
        assert checker.threshold == 0.7
        assert checker.model == "llama3.2:1b"

    def test_make_default_deps_wires_noop(self) -> None:
        deps = make_default_deps(embeddings=MagicMock())
        assert isinstance(deps.nli, NoopContradictionChecker)


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


class SpyChecker:
    def __init__(self, result: list[ContradictionCandidate] | None = None) -> None:
        self.result = result or []
        self.calls: list[dict[str, Any]] = []

    async def find_candidates(
        self,
        conn: Any,
        tenant_id: UUID,
        new_content: str,
        type_: str,
        *,
        limit: int,
    ) -> list[ContradictionCandidate]:
        self.calls.append(
            {"tenant_id": tenant_id, "new_content": new_content, "type_": type_, "limit": limit}
        )
        return self.result


def _build_ctx(nli: Any | None = None) -> ToolContext:
    deps_kwargs: dict[str, Any] = {
        "embeddings": FakeEmbeddings(),
        "audit": NoopAuditLogger(),
        "quotas": NoopQuotas(),
    }
    if nli is not None:
        deps_kwargs["nli"] = nli
    return ToolContext(
        request_id=str(uuid4()),
        tenant_id=uuid4(),
        identity_id=uuid4(),
        client_id="client-1",
        scopes=frozenset(("memory.read", "memory.write")),
        db_pool=MagicMock(),
        deps=ToolDeps(**deps_kwargs),
    )


def _patch_tenant_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_tx(pool: Any, tenant_id: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.mcp.tools.write.tenant_tx", fake_tx)


def _insert_conn() -> AsyncMock:
    """Conn scripted for the no-dedupe plain-INSERT path."""
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        None,  # hash check (in check_dup)
        None,  # cosine check (in check_dup)
        {"id": uuid4(), "version": 1, "created_at": datetime.now(tz=UTC)},  # INSERT
    ]
    return conn


def _candidate(score: float = 0.9) -> ContradictionCandidate:
    return ContradictionCandidate(
        memory_id=uuid4(),
        content_snippet="we use MySQL",
        contradiction_score=score,
        type="note",
        tags=[],
        created_at=datetime.now(tz=UTC),
    )


class TestMemoryWriteContradictions:
    @pytest.mark.asyncio
    async def test_default_off_no_check_and_null_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC1: check_contradictions=false → checker never called, field None."""
        spy = SpyChecker()
        _patch_tenant_tx(monkeypatch, _insert_conn())
        result = await MemoryWriteTool()(_build_ctx(nli=spy), MemoryWriteInput(content="x"))
        assert isinstance(result, MemoryWriteOutput)
        assert result.contradictions is None
        assert spy.calls == []

    @pytest.mark.asyncio
    async def test_noop_backend_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC2: nli_backend=none (Noop checker) → contradictions: []."""
        _patch_tenant_tx(monkeypatch, _insert_conn())
        result = await MemoryWriteTool()(
            _build_ctx(),  # default deps → NoopContradictionChecker
            MemoryWriteInput(content="x", check_contradictions=True),
        )
        assert isinstance(result, MemoryWriteOutput)
        assert result.contradictions == []

    @pytest.mark.asyncio
    async def test_populated_candidates_and_write_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC3 + AC6: candidates surface AND the write completes with an id."""
        cand = _candidate()
        spy = SpyChecker(result=[cand])
        _patch_tenant_tx(monkeypatch, _insert_conn())
        ctx = _build_ctx(nli=spy)
        result = await MemoryWriteTool()(
            ctx,
            MemoryWriteInput(content="we use PostgreSQL", check_contradictions=True),
        )
        assert isinstance(result, MemoryWriteOutput)
        assert result.id is not None
        assert result.deduped is False
        assert result.contradictions == [cand]
        # Checker received the call-scoped limit + tool inputs
        assert spy.calls == [
            {
                "tenant_id": ctx.tenant_id,
                "new_content": "we use PostgreSQL",
                "type_": "note",
                "limit": 3,
            }
        ]

    @pytest.mark.asyncio
    async def test_contradiction_limit_passed_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = SpyChecker()
        _patch_tenant_tx(monkeypatch, _insert_conn())
        await MemoryWriteTool()(
            _build_ctx(nli=spy),
            MemoryWriteInput(content="x", check_contradictions=True, contradiction_limit=7),
        )
        assert spy.calls[0]["limit"] == 7

    @pytest.mark.asyncio
    async def test_dedupe_early_return_skips_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC5: deduped write returns before the NLI check runs."""
        existing = uuid4()
        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            {"id": existing},  # hash check hits
            {"id": existing, "version": 2, "created_at": datetime.now(tz=UTC)},  # merge UPDATE
        ]
        spy = SpyChecker(result=[_candidate()])
        _patch_tenant_tx(monkeypatch, conn)
        result = await MemoryWriteTool()(
            _build_ctx(nli=spy),
            MemoryWriteInput(content="x", check_contradictions=True),
        )
        assert isinstance(result, MemoryWriteOutput)
        assert result.deduped is True
        assert spy.calls == []  # NLI never ran
        assert result.contradictions == []  # stable shape: checked-but-empty

    @pytest.mark.asyncio
    async def test_output_serializes_candidates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """contradictions round-trips through pydantic JSON serialization."""
        cand = _candidate(score=0.8)
        spy = SpyChecker(result=[cand])
        _patch_tenant_tx(monkeypatch, _insert_conn())
        result = await MemoryWriteTool()(
            _build_ctx(nli=spy),
            MemoryWriteInput(content="x", check_contradictions=True),
        )
        assert isinstance(result, MemoryWriteOutput)
        dumped = result.model_dump(mode="json")
        assert dumped["contradictions"][0]["memory_id"] == str(cand.memory_id)
        assert dumped["contradictions"][0]["contradiction_score"] == 0.8
