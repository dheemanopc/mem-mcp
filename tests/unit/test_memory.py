"""Tests for mem_mcp.memory primitives (T-5.5/5.6/5.7/5.8)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mem_mcp.memory.dedupe import DedupeMatch, check_dup
from mem_mcp.memory.hybrid_query import SearchParams, SearchResult, hybrid_search
from mem_mcp.memory.normalize import hash_content, normalize_for_hash
from mem_mcp.memory.recency import (
    DEFAULT_RECENCY_LAMBDA,
    RECENCY_LAMBDA_BY_TYPE,
    recency_lambda_for,
)
from mem_mcp.memory.versioning import NON_VERSIONED_TYPES, VERSIONED_TYPES


# --------------------------------------------------------------------------
# normalize.py (T-5.5)
# --------------------------------------------------------------------------


class TestNormalizeForHash:
    def test_lowercases(self) -> None:
        assert normalize_for_hash("Hello WORLD") == "hello world"

    def test_strips_outer_whitespace(self) -> None:
        assert normalize_for_hash("  hi  ") == "hi"

    def test_collapses_internal_whitespace(self) -> None:
        assert normalize_for_hash("a   b\t\nc") == "a b c"

    def test_nfkc_normalize(self) -> None:
        # 'ﬃ' (U+FB03 LATIN SMALL LIGATURE FFI) → NFKC → 'ffi'
        assert normalize_for_hash("oﬃce") == "office"

    def test_empty_string(self) -> None:
        assert normalize_for_hash("") == ""

    def test_only_whitespace(self) -> None:
        assert normalize_for_hash("   \t \n") == ""

    def test_non_string_raises(self) -> None:
        with pytest.raises(TypeError):
            normalize_for_hash(42)  # type: ignore[arg-type]

    @given(s=st.text(min_size=0, max_size=200))
    @settings(max_examples=50, deadline=None)
    def test_idempotent(self, s: str) -> None:
        once = normalize_for_hash(s)
        twice = normalize_for_hash(once)
        assert once == twice


class TestHashContent:
    def test_deterministic(self) -> None:
        assert hash_content("hello") == hash_content("hello")

    def test_normalization_invariance(self) -> None:
        # All collapse to "hello world" → same hash
        assert hash_content("Hello WORLD") == hash_content("  hello   world  ") == hash_content("hello world")

    def test_different_content_different_hash(self) -> None:
        assert hash_content("a") != hash_content("b")

    def test_64_hex_chars(self) -> None:
        h = hash_content("anything")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# --------------------------------------------------------------------------
# recency.py (T-5.8)
# --------------------------------------------------------------------------


class TestRecencyLambda:
    @pytest.mark.parametrize("type_,expected", [
        ("decision", 0.0019),
        ("fact", 0.0019),
        ("note", 0.05),
        ("snippet", 0.10),
        ("question", 0.05),
    ])
    def test_known_types(self, type_: str, expected: float) -> None:
        assert recency_lambda_for(type_) == expected

    def test_unknown_type_uses_default(self) -> None:
        assert recency_lambda_for("unknown") == DEFAULT_RECENCY_LAMBDA

    def test_none_uses_default(self) -> None:
        assert recency_lambda_for(None) == DEFAULT_RECENCY_LAMBDA

    def test_table_completeness(self) -> None:
        # All 5 spec'd types covered
        assert set(RECENCY_LAMBDA_BY_TYPE.keys()) == {"decision", "fact", "note", "snippet", "question"}


# --------------------------------------------------------------------------
# versioning.py (helper constants only for now)
# --------------------------------------------------------------------------


class TestVersioningConstants:
    def test_disjoint(self) -> None:
        assert VERSIONED_TYPES.isdisjoint(NON_VERSIONED_TYPES)

    def test_complete(self) -> None:
        all_types = VERSIONED_TYPES | NON_VERSIONED_TYPES
        assert all_types == {"decision", "fact", "note", "snippet", "question"}


# --------------------------------------------------------------------------
# dedupe.py (T-5.6)
# --------------------------------------------------------------------------


class _FakeConn:
    """Fake asyncpg.Connection that lets tests script .fetchrow returns."""

    def __init__(self, returns: list[Any]) -> None:
        self.returns = list(returns)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        return self.returns.pop(0) if self.returns else None


class TestCheckDup:
    @pytest.mark.asyncio
    async def test_hash_hit_short_circuits(self) -> None:
        existing = uuid4()
        conn = _FakeConn(returns=[{"id": existing}])
        match = await check_dup(conn, uuid4(), "h", [0.0] * 1024, "note")  # type: ignore[arg-type]
        assert match == DedupeMatch(existing_id=existing, kind="hash")
        assert len(conn.calls) == 1  # second query NOT made

    @pytest.mark.asyncio
    async def test_no_hash_no_embedding_returns_none(self) -> None:
        conn = _FakeConn(returns=[None])
        match = await check_dup(conn, uuid4(), "h", None, "note")  # type: ignore[arg-type]
        assert match is None
        assert len(conn.calls) == 1  # cosine query skipped (no embedding)

    @pytest.mark.asyncio
    async def test_no_hash_embedding_above_threshold(self) -> None:
        existing = uuid4()
        conn = _FakeConn(returns=[None, {"id": existing, "sim": 0.97}])
        match = await check_dup(conn, uuid4(), "h", [0.0] * 1024, "note")  # type: ignore[arg-type]
        assert match == DedupeMatch(existing_id=existing, kind="embedding")
        assert len(conn.calls) == 2

    @pytest.mark.asyncio
    async def test_embedding_below_threshold_returns_none(self) -> None:
        conn = _FakeConn(returns=[None, {"id": uuid4(), "sim": 0.50}])
        match = await check_dup(conn, uuid4(), "h", [0.0] * 1024, "note")  # type: ignore[arg-type]
        assert match is None

    @pytest.mark.asyncio
    async def test_embedding_at_threshold_returns_none(self) -> None:
        # Strict > 0.95 per spec
        conn = _FakeConn(returns=[None, {"id": uuid4(), "sim": 0.95}])
        match = await check_dup(conn, uuid4(), "h", [0.0] * 1024, "note")  # type: ignore[arg-type]
        assert match is None

    @pytest.mark.asyncio
    async def test_embedding_query_includes_type(self) -> None:
        """Spec §10.5: the cosine probe is type-scoped."""
        conn = _FakeConn(returns=[None, None])
        await check_dup(conn, uuid4(), "h", [0.0] * 1024, "decision")  # type: ignore[arg-type]
        # 2nd call's args: tenant_id, type_, embedding
        _, args = conn.calls[1]
        assert args[1] == "decision"


# --------------------------------------------------------------------------
# hybrid_query.py (T-5.7)
# --------------------------------------------------------------------------


class _FakeFetchConn:
    """Fake asyncpg.Connection.fetch that returns canned rows."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append((query, args))
        return list(self.rows)


def _row(score: float = 0.5, **overrides: Any) -> dict[str, Any]:
    base = {
        "id": uuid4(),
        "content": "x",
        "type": "note",
        "tags": ["a"],
        "version": 1,
        "created_at": datetime.now(tz=timezone.utc),
        "updated_at": datetime.now(tz=timezone.utc),
        "sem_score": 0.5,
        "kw_score": 0.5,
        "recency_factor": 1.0,
        "score": score,
    }
    base.update(overrides)
    return base


class TestHybridSearch:
    @pytest.mark.asyncio
    async def test_returns_search_results(self) -> None:
        rows = [_row(score=0.9), _row(score=0.5)]
        conn = _FakeFetchConn(rows)
        params = SearchParams(
            qvec=[0.0] * 1024,
            qtxt="hello",
            type_=None,
            tags=None,
            since=None,
            until=None,
            limit=10,
            recency_lambda=0.05,
        )
        results = await hybrid_search(conn, uuid4(), params)  # type: ignore[arg-type]
        assert len(results) == 2
        assert all(isinstance(r, SearchResult) for r in results)

    @pytest.mark.asyncio
    async def test_passes_all_11_positional_params(self) -> None:
        conn = _FakeFetchConn([])
        tenant = uuid4()
        params = SearchParams(
            qvec=[0.1] * 1024,
            qtxt="hello world",
            type_="decision",
            tags=["project:ew"],
            since=datetime(2025, 1, 1, tzinfo=timezone.utc),
            until=datetime(2026, 1, 1, tzinfo=timezone.utc),
            limit=15,
            recency_lambda=0.0019,
            w_sem=0.8,
            w_kw=0.2,
        )
        await hybrid_search(conn, tenant, params)  # type: ignore[arg-type]
        assert len(conn.calls) == 1
        _, args = conn.calls[0]
        assert len(args) == 11
        assert args[0] == [0.1] * 1024
        assert args[1] == tenant
        assert args[2] == "decision"
        assert args[3] == ["project:ew"]
        assert args[6] == "hello world"
        assert args[7] == 0.0019
        assert args[8] == 0.8
        assert args[9] == 0.2
        assert args[10] == 15

    @pytest.mark.asyncio
    async def test_default_weights(self) -> None:
        from mem_mcp.memory.hybrid_query import SEARCH_DEFAULT_W_KW, SEARCH_DEFAULT_W_SEM
        assert SEARCH_DEFAULT_W_SEM == 0.7
        assert SEARCH_DEFAULT_W_KW == 0.3
        # Both default if not specified
        params = SearchParams(
            qvec=[0.0] * 1024, qtxt="x",
            type_=None, tags=None, since=None, until=None,
            limit=1, recency_lambda=0.05,
        )
        assert params.w_sem == 0.7
        assert params.w_kw == 0.3
