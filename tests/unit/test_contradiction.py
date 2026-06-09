"""Unit tests for mem_mcp.memory.contradiction."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mem_mcp.memory.contradiction import (
    ContradictionCandidate,
    NLI_DEFAULT_THRESHOLD,
    _parse_nli_response,
    _score_contradiction,
    find_contradiction_candidates,
)


# ---------------------------------------------------------------------------
# _parse_nli_response
# ---------------------------------------------------------------------------


def test_parse_contradiction():
    label, score = _parse_nli_response("CONTRADICTION 0.9")
    assert label == "CONTRADICTION"
    assert score == pytest.approx(0.9)


def test_parse_neutral():
    label, score = _parse_nli_response("NEUTRAL 0.1")
    assert label == "NEUTRAL"
    assert score == pytest.approx(0.1)


def test_parse_entailment():
    label, score = _parse_nli_response("ENTAILMENT 0.75")
    assert label == "ENTAILMENT"
    assert score == pytest.approx(0.75)


def test_parse_garbage_defaults_neutral():
    label, score = _parse_nli_response("I cannot determine this.")
    assert label == "NEUTRAL"
    assert score == 0.0


def test_parse_score_clamped_above_one():
    label, score = _parse_nli_response("CONTRADICTION 1.5")
    assert label == "CONTRADICTION"
    assert score == pytest.approx(1.0)


def test_parse_score_clamped_below_zero():
    label, score = _parse_nli_response("NEUTRAL -0.3")
    assert label == "NEUTRAL"
    assert score == pytest.approx(0.0)


def test_parse_case_insensitive():
    label, score = _parse_nli_response("contradiction 0.82")
    assert label == "CONTRADICTION"
    assert score == pytest.approx(0.82)


# ---------------------------------------------------------------------------
# _score_contradiction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_contradiction_returns_score_for_contradiction():
    with patch("mem_mcp.memory.contradiction._score_contradiction") as mock_score:
        mock_score.return_value = 0.9
        result = await mock_score("we use MySQL", "we use PostgreSQL", "http://ollama", "llama3.2:1b")
    assert result == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_score_contradiction_returns_zero_for_neutral():
    """NEUTRAL response → score returned as 0.0."""
    import httpx
    from unittest.mock import AsyncMock

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "NEUTRAL 0.9"}
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        score = await _score_contradiction("a", "b", "http://ollama", "llama3.2:1b")

    assert score == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_score_contradiction_parses_contradiction_label():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "CONTRADICTION 0.85"}
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        score = await _score_contradiction("we use MySQL", "we use PostgreSQL", "http://ollama", "llama3.2:1b")

    assert score == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# find_contradiction_candidates
# ---------------------------------------------------------------------------


def _make_row(memory_id: uuid.UUID | None = None, content: str = "old content") -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": memory_id or uuid.uuid4(),
        "content": content,
        "type": "fact",
        "tags": ["db"],
        "created_at": datetime(2025, 1, 1, tzinfo=UTC),
    }[key]
    return row


@pytest.mark.asyncio
async def test_find_returns_empty_when_no_rows():
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])

    results = await find_contradiction_candidates(
        conn, uuid.uuid4(), "new content", "fact", "http://ollama"
    )
    assert results == []


@pytest.mark.asyncio
async def test_find_returns_candidate_above_threshold():
    mem_id = uuid.uuid4()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[_make_row(mem_id, "we use MySQL")])

    with patch("mem_mcp.memory.contradiction._score_contradiction", AsyncMock(return_value=0.88)):
        results = await find_contradiction_candidates(
            conn, uuid.uuid4(), "we use PostgreSQL", "fact", "http://ollama",
            threshold=0.7,
        )

    assert len(results) == 1
    assert results[0].memory_id == mem_id
    assert results[0].contradiction_score == pytest.approx(0.88)


@pytest.mark.asyncio
async def test_find_excludes_candidate_below_threshold():
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[_make_row()])

    with patch("mem_mcp.memory.contradiction._score_contradiction", AsyncMock(return_value=0.5)):
        results = await find_contradiction_candidates(
            conn, uuid.uuid4(), "new content", "fact", "http://ollama",
            threshold=0.7,
        )

    assert results == []


@pytest.mark.asyncio
async def test_find_skips_candidate_on_ollama_error():
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[_make_row(), _make_row()])

    async def flaky_score(a, b, url, model):
        raise ConnectionError("ollama down")

    with patch("mem_mcp.memory.contradiction._score_contradiction", side_effect=flaky_score):
        results = await find_contradiction_candidates(
            conn, uuid.uuid4(), "new content", "fact", "http://ollama"
        )

    assert results == []


@pytest.mark.asyncio
async def test_find_respects_limit():
    rows = [_make_row() for _ in range(5)]
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)

    with patch("mem_mcp.memory.contradiction._score_contradiction", AsyncMock(return_value=0.9)):
        results = await find_contradiction_candidates(
            conn, uuid.uuid4(), "new content", "fact", "http://ollama",
            threshold=0.7, limit=2,
        )

    assert len(results) == 2


@pytest.mark.asyncio
async def test_find_sorts_by_score_descending():
    rows = [_make_row(content="a"), _make_row(content="b"), _make_row(content="c")]
    scores = {"a": 0.75, "b": 0.95, "c": 0.82}
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)

    async def varying_score(new, candidate, url, model):
        return scores.get(candidate, 0.0)

    with patch("mem_mcp.memory.contradiction._score_contradiction", side_effect=varying_score):
        results = await find_contradiction_candidates(
            conn, uuid.uuid4(), "new content", "fact", "http://ollama",
            threshold=0.7, limit=3,
        )

    assert [r.contradiction_score for r in results] == [0.95, 0.82, 0.75]
