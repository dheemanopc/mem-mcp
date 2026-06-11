"""Tests for textual previews + lenient keyword matching (search & list).

User-facing changes:
- memory_search keyword leg is OR-of-lexemes (any word can match) instead of
  plainto_tsquery's all-words AND.
- search results carry `preview` (ts_headline snippet) and `content` capped
  at 2000 chars with content_truncated/content_length flags.
- memory_list accepts `keywords` — returns only textual matches, with a
  keyword-windowed preview.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
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
from mem_mcp.mcp.tools.list import MemoryListInput, MemoryListOutput, MemoryListTool
from mem_mcp.mcp.tools.search import (
    SEARCH_CONTENT_MAX_CHARS,
    MemorySearchInput,
    MemorySearchOutput,
    MemorySearchTool,
)
from mem_mcp.memory.hybrid_query import _HYBRID_SQL, SearchResult, hybrid_search

# --------------------------------------------------------------------------
# Lenient keyword SQL
# --------------------------------------------------------------------------


class TestLenientHybridSql:
    def test_keyword_leg_is_or_of_lexemes(self) -> None:
        """plainto_tsquery (AND of all words) is gone; OR aggregation is in."""
        assert "plainto_tsquery" not in _HYBRID_SQL
        assert "string_agg(quote_literal(lexeme), ' | ')" in _HYBRID_SQL

    def test_null_tsquery_guard_present(self) -> None:
        """All-stopword queries yield a NULL tsquery — keyword leg must skip."""
        assert "q.tsq IS NOT NULL" in _HYBRID_SQL

    def test_preview_selected_after_limit(self) -> None:
        """ts_headline runs on the LIMITed rows, not the whole candidate set."""
        assert "ts_headline" in _HYBRID_SQL
        assert _HYBRID_SQL.index("LIMIT $11") < _HYBRID_SQL.index("ts_headline")


class _FakeFetchConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append((query, args))
        return list(self.rows)


def _search_row(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": uuid4(),
        "content": "we use postgres",
        "preview": "we use **postgres**",
        "type": "note",
        "tags": ["a"],
        "version": 1,
        "created_at": datetime.now(tz=UTC),
        "updated_at": datetime.now(tz=UTC),
        "sem_score": 0.5,
        "kw_score": 0.5,
        "recency_factor": 1.0,
        "score": 0.5,
        "indexable": True,
        "embedding_status": "ok",
    }
    base.update(overrides)
    return base


class TestHybridSearchPreview:
    @pytest.mark.asyncio
    async def test_preview_mapped_from_row(self) -> None:
        from mem_mcp.memory.hybrid_query import SearchParams

        conn = _FakeFetchConn([_search_row()])
        params = SearchParams(
            qvec=[0.0] * 1024,
            qtxt="postgres",
            type_=None,
            tags=None,
            since=None,
            until=None,
            limit=10,
            recency_lambda=0.05,
        )
        results = await hybrid_search(conn, uuid4(), params)
        assert results[0].preview == "we use **postgres**"


# --------------------------------------------------------------------------
# MemorySearchTool output shape
# --------------------------------------------------------------------------


class _FakeEmbeddings:
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
            embeddings=_FakeEmbeddings(),
            audit=NoopAuditLogger(),
            quotas=NoopQuotas(),
        ),
    )


def _patch_tx(monkeypatch: pytest.MonkeyPatch, module: str, conn: Any) -> None:
    @asynccontextmanager
    async def fake_tx(pool: Any, tenant_id: Any) -> Any:
        yield conn

    monkeypatch.setattr(f"{module}.tenant_tx", fake_tx)


def _search_result(content: str, preview: str = "snippet") -> SearchResult:
    return SearchResult(
        id=uuid4(),
        content=content,
        type="note",
        tags=[],
        version=1,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        sem_score=0.9,
        kw_score=0.5,
        recency_factor=1.0,
        score=0.8,
        indexable=True,
        embedding_status="ok",
        preview=preview,
    )


class TestSearchToolTruncation:
    @pytest.mark.asyncio
    async def test_long_content_truncated_with_flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        long_content = "y" * (SEARCH_CONTENT_MAX_CHARS + 500)

        async def fake_hybrid(conn: Any, tenant_id: Any, params: Any) -> Any:
            return [_search_result(long_content, preview="head of y")]

        monkeypatch.setattr("mem_mcp.mcp.tools.search.hybrid_search", fake_hybrid)
        _patch_tx(monkeypatch, "mem_mcp.mcp.tools.search", AsyncMock())

        out = await MemorySearchTool()(_build_ctx(), MemorySearchInput(query="x"))
        assert isinstance(out, MemorySearchOutput)
        item = out.results[0]
        assert len(item.content) == SEARCH_CONTENT_MAX_CHARS
        assert item.content_truncated is True
        assert item.content_length == SEARCH_CONTENT_MAX_CHARS + 500
        assert item.preview == "head of y"

    @pytest.mark.asyncio
    async def test_short_content_not_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_hybrid(conn: Any, tenant_id: Any, params: Any) -> Any:
            return [_search_result("short body", preview="short body")]

        monkeypatch.setattr("mem_mcp.mcp.tools.search.hybrid_search", fake_hybrid)
        _patch_tx(monkeypatch, "mem_mcp.mcp.tools.search", AsyncMock())

        out = await MemorySearchTool()(_build_ctx(), MemorySearchInput(query="x"))
        assert isinstance(out, MemorySearchOutput)
        item = out.results[0]
        assert item.content == "short body"
        assert item.content_truncated is False
        assert item.content_length == len("short body")


# --------------------------------------------------------------------------
# memory_list keywords filter
# --------------------------------------------------------------------------


def _list_row(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": uuid4(),
        "content": "postgres notes",
        "preview": "**postgres** notes",
        "type": "note",
        "tags": ["a"],
        "version": 1,
        "is_current": True,
        "created_at": datetime.now(tz=UTC),
        "updated_at": datetime.now(tz=UTC),
        "deleted_at": None,
        "expires_at": None,
        "indexable": True,
        "embedding_status": "ok",
    }
    base.update(overrides)
    return base


class TestListKeywords:
    def test_keywords_validation(self) -> None:
        with pytest.raises(ValidationError):
            MemoryListInput.model_validate({"keywords": ""})
        with pytest.raises(ValidationError):
            MemoryListInput.model_validate({"keywords": "x" * 513})
        assert MemoryListInput.model_validate({}).keywords is None
        assert MemoryListInput.model_validate({"keywords": "postgres"}).keywords == "postgres"

    @pytest.mark.asyncio
    async def test_keywords_adds_tsv_filter_and_headline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = MagicMock()
        conn.fetch = AsyncMock(return_value=[_list_row()])
        _patch_tx(monkeypatch, "mem_mcp.mcp.tools.list", conn)

        out = await MemoryListTool()(_build_ctx(), MemoryListInput(keywords="postgres choice"))
        assert isinstance(out, MemoryListOutput)
        assert out.results[0].preview == "**postgres** notes"

        query = conn.fetch.call_args[0][0]
        args = conn.fetch.call_args[0][1:]
        assert "content_tsv @@" in query
        assert "ts_headline" in query
        assert "postgres choice" in args  # keyword param passed through

    @pytest.mark.asyncio
    async def test_no_keywords_uses_head_preview_and_no_filter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = MagicMock()
        conn.fetch = AsyncMock(return_value=[_list_row(preview="postgres notes")])
        _patch_tx(monkeypatch, "mem_mcp.mcp.tools.list", conn)

        out = await MemoryListTool()(_build_ctx(), MemoryListInput())
        assert isinstance(out, MemoryListOutput)
        assert out.results[0].preview == "postgres notes"

        query = conn.fetch.call_args[0][0]
        assert "content_tsv" not in query
        assert "ts_headline" not in query
        assert "LEFT(content, 280) AS preview" in query

    @pytest.mark.asyncio
    async def test_keywords_with_cursor_param_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cursor params must come after the keywords param (positional SQL)."""
        from mem_mcp.mcp.tools.list import _encode_cursor

        conn = MagicMock()
        conn.fetch = AsyncMock(return_value=[])
        _patch_tx(monkeypatch, "mem_mcp.mcp.tools.list", conn)

        cursor = _encode_cursor(datetime.now(tz=UTC), uuid4())
        out = await MemoryListTool()(
            _build_ctx(), MemoryListInput(keywords="postgres", cursor=cursor)
        )
        assert isinstance(out, MemoryListOutput)
        assert out.results == []

        query = conn.fetch.call_args[0][0]
        args = conn.fetch.call_args[0][1:]
        # tenant, keywords, cursor-value, cursor-id, limit
        assert args[1] == "postgres"
        assert "content_tsv @@" in query
