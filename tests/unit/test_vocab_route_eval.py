"""Tests for the space-vocabulary Phase 0 eval harness (spec a12959d7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evals.vocab_route.phrases import build_vocabulary, extract_phrases
from evals.vocab_route.run_eval import (
    MockBackend,
    StubEmbedder,
    build_space_vocab,
    hit_at_5,
    mrr,
    route_phrases,
    rrf_fuse,
    run_eval,
)

# --------------------------------------------------------------------------
# Phrase extraction
# --------------------------------------------------------------------------


class TestExtractPhrases:
    def test_stopword_boundaries(self) -> None:
        phrases = extract_phrases("the dwell pivot detection runs after the close")
        assert "dwell pivot detection" in phrases

    def test_no_unigrams(self) -> None:
        phrases = extract_phrases("postgres is a database")
        assert all(len(p.split()) >= 2 for p in phrases)

    def test_phrases_capped_at_four_words(self) -> None:
        phrases = extract_phrases("alpha beta gamma delta epsilon zeta")
        assert all(len(p.split()) <= 4 for p in phrases)

    def test_phrases_do_not_cross_sentence_boundaries(self) -> None:
        """'dismissed_at null. repro: reminder' is vocabulary noise — phrases
        stop at terminal punctuation. (Validated empirically: fixing this
        moved config B hit@5 0.89 -> 0.94 on the 21-doc preliminary run.)"""
        phrases = extract_phrases("state stays dismissed_at null. repro: reminder fires again")
        assert not any("null. repro" in p for p in phrases)
        assert not any(p.endswith((".", ":", ";")) for p in phrases)
        # identifiers with interior dots survive (no space after the dot)
        phrases2 = extract_phrases("crash inside get_batch.py slug branch")
        assert any("get_batch.py" in p for p in phrases2)

    def test_deterministic(self) -> None:
        text = "chunk level vector retrieval for long documents"
        assert extract_phrases(text) == extract_phrases(text)

    def test_build_vocabulary_frequency_ranking(self) -> None:
        docs = ["retry budget rules"] * 3 + ["dwell pivot detection"]
        vocab = build_vocabulary(docs, max_size=10)
        assert vocab[0] == ("retry budget rules", 3)

    def test_build_vocabulary_max_size(self) -> None:
        docs = [f"unique phrase {i} marker" for i in range(50)]
        assert len(build_vocabulary(docs, max_size=5)) == 5


# --------------------------------------------------------------------------
# Metrics + fusion
# --------------------------------------------------------------------------


class TestMetrics:
    def test_hit_at_5(self) -> None:
        assert hit_at_5(["a", "b", "c", "d", "e"], ["e"]) is True
        assert hit_at_5(["a", "b", "c", "d", "e", "f"], ["f"]) is False

    def test_mrr(self) -> None:
        assert mrr(["x", "a"], ["a"]) == 0.5
        assert mrr(["x"], ["a"]) == 0.0

    def test_rrf_prefers_doubly_ranked(self) -> None:
        fused = rrf_fuse(["a", "b"], ["b", "c"])
        assert fused[0] == "b"


# --------------------------------------------------------------------------
# Vocabulary build + routing
# --------------------------------------------------------------------------


class TestVocabBuild:
    @pytest.mark.asyncio
    async def test_dedup_keeps_higher_frequency(self) -> None:
        # Stub embedder hashes lowercase text — identical text = identical
        # vector → cosine 1.0 → deduped.
        docs = ["alpha beta gamma"] * 5
        vocab = await build_space_vocab(StubEmbedder(), docs)
        phrases = [p for p, _, _ in vocab]
        assert len(phrases) == len(set(phrases))

    @pytest.mark.asyncio
    async def test_route_phrases_floor_and_cap(self) -> None:
        emb = StubEmbedder()
        vocab = await build_space_vocab(emb, ["dwell pivot detection near close"])
        qvec = (await emb.embed("dwell pivot detection")).vector
        routed = route_phrases(qvec, vocab)
        assert len(routed) <= 8
        assert all(score >= 0.75 for _, score in routed)
        # The exact phrase exists in vocab → stub cosine 1.0 → routed.
        assert any(p == "dwell pivot detection" for p, _ in routed)


# --------------------------------------------------------------------------
# End-to-end in mock mode
# --------------------------------------------------------------------------


def _corpus() -> list[dict[str, Any]]:
    return [
        {
            "id": "m-target",
            "content": "The dwell pivot detection threshold was raised to 0.8 "
            "after the false-positive review. dwell pivot detection applies "
            "only to hourly bars.",
            "tags": ["trader"],
        },
        {
            "id": "m-noise-1",
            "content": "Weekly grocery planning notes and errands.",
            "tags": ["trader"],
        },
        {
            "id": "m-noise-2",
            "content": "Postgres connection pooling configuration for the api.",
            "tags": ["core"],
        },
    ]


class TestRunEvalMock:
    @pytest.mark.asyncio
    async def test_report_shape_and_routing(self) -> None:
        embedder = StubEmbedder()
        backend = MockBackend(_corpus(), embedder)
        cases = [
            {
                "id": "c1",
                "space": "trader",
                # Exact project phrase → vocab route must find it
                "query": "dwell pivot detection",
                "expected_ids": ["m-target"],
            }
        ]
        report = await run_eval(backend, embedder, cases, {"trader": ["trader"], "core": ["core"]})
        assert report["n_cases"] == 1
        assert set(report["summary"].keys()) == {"A", "B", "C"}
        case = report["per_case"][0]
        # B routed the exact phrase and keyword-matched the target memory
        assert "dwell pivot detection" in case["routed_phrases"]
        assert case["B"]["hit@5"] is True
        assert isinstance(report["viability_gate_passed"], bool)

    @pytest.mark.asyncio
    async def test_thin_route_falls_back_to_vector(self) -> None:
        embedder = StubEmbedder()
        backend = MockBackend(_corpus(), embedder)
        cases = [
            {
                "id": "c2",
                "space": "core",
                # No vocab phrase will route for this in the 'core' space
                "query": "zzz qqq completely unrelated nonsense",
                "expected_ids": ["m-noise-2"],
            }
        ]
        report = await run_eval(backend, embedder, cases, {"core": ["core"]})
        assert report["per_case"][0]["b_fell_back"] is True


# --------------------------------------------------------------------------
# Shipped dataset sanity
# --------------------------------------------------------------------------


class TestShippedDataset:
    def test_dataset_parses_and_spaces_resolve(self) -> None:
        root = Path(__file__).parents[2] / "evals" / "vocab_route"
        spaces = json.loads((root / "spaces.json").read_text())
        cases = [
            json.loads(line)
            for line in (root / "dataset.jsonl").read_text().splitlines()
            if line.strip()
        ]
        assert len(cases) >= 18
        for case in cases:
            assert case["space"] in spaces
            assert case["query"]
            assert case["expected_ids"]
