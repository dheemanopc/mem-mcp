"""Unit tests for the retrieval evaluation harness (T-9.5)."""

import json
import tempfile
from pathlib import Path

import pytest

from evals.retrieval.run_eval import (
    InMemoryStore,
    StubEmbedder,
    aggregate,
    mrr,
    run_eval,
    top3_hit,
)


class TestTop3Hit:
    """Test the top3_hit metric function."""

    def test_top3_hit_when_any_expected_in_first_3(self) -> None:
        """Expected ID in first 3 retrieved → True."""
        retrieved = ["a", "b", "c", "d"]
        expected = ["c"]
        assert top3_hit(retrieved, expected) is True

    def test_top3_hit_false_when_expected_not_in_first_3(self) -> None:
        """Expected ID beyond first 3 → False."""
        retrieved = ["x", "y", "z", "a"]
        expected = ["a"]
        assert top3_hit(retrieved, expected) is False

    def test_top3_hit_multiple_expected_one_in_first_3(self) -> None:
        """Multiple expected, one in first 3 → True."""
        retrieved = ["a", "b", "c"]
        expected = ["b", "x"]
        assert top3_hit(retrieved, expected) is True

    def test_top3_hit_empty_retrieved(self) -> None:
        """Empty retrieved list → False."""
        retrieved: list[str] = []
        expected = ["a"]
        assert top3_hit(retrieved, expected) is False

    def test_top3_hit_empty_expected(self) -> None:
        """Empty expected list → False (no match possible)."""
        retrieved = ["a", "b", "c"]
        expected: list[str] = []
        assert top3_hit(retrieved, expected) is False


class TestMrr:
    """Test the MRR (mean reciprocal rank) metric function."""

    def test_mrr_first_match_at_rank_1(self) -> None:
        """First expected at rank 1 → MRR = 1.0."""
        retrieved = ["a", "b"]
        expected = ["a"]
        assert mrr(retrieved, expected) == 1.0

    def test_mrr_first_match_at_rank_3(self) -> None:
        """First expected at rank 3 → MRR = 1/3."""
        retrieved = ["x", "y", "a"]
        expected = ["a"]
        assert abs(mrr(retrieved, expected) - 1.0 / 3.0) < 1e-6

    def test_mrr_no_match_zero(self) -> None:
        """No expected in retrieved → MRR = 0.0."""
        retrieved = ["x", "y", "z"]
        expected = ["a"]
        assert mrr(retrieved, expected) == 0.0

    def test_mrr_multiple_expected_uses_first(self) -> None:
        """Multiple expected; only first match counts."""
        retrieved = ["x", "b", "a"]
        expected = ["a", "b"]
        # 'b' is at rank 2
        assert abs(mrr(retrieved, expected) - 1.0 / 2.0) < 1e-6

    def test_mrr_empty_retrieved(self) -> None:
        """Empty retrieved → MRR = 0.0."""
        retrieved: list[str] = []
        expected = ["a"]
        assert mrr(retrieved, expected) == 0.0

    def test_mrr_empty_expected(self) -> None:
        """Empty expected → MRR = 0.0."""
        retrieved = ["a", "b"]
        expected: list[str] = []
        assert mrr(retrieved, expected) == 0.0


class TestAggregate:
    """Test the aggregate function."""

    def test_aggregate_groups_by_category(self) -> None:
        """Aggregate per_case results by category."""
        per_case = [
            {
                "id": "case-1",
                "category": "exact_recall",
                "top3_hit": True,
                "mrr": 1.0,
            },
            {
                "id": "case-2",
                "category": "exact_recall",
                "top3_hit": False,
                "mrr": 0.0,
            },
            {
                "id": "case-3",
                "category": "paraphrase",
                "top3_hit": True,
                "mrr": 0.5,
            },
        ]

        result = aggregate(per_case)

        # Check structure
        assert "by_category" in result
        assert "overall" in result

        # Check exact_recall aggregation: 1 hit out of 2 → 0.5 top3_hit_rate
        assert result["by_category"]["exact_recall"]["top3_hit_rate"] == 0.5
        assert abs(result["by_category"]["exact_recall"]["mrr"] - 0.5) < 1e-6

        # Check paraphrase aggregation: 1 hit out of 1 → 1.0 top3_hit_rate
        assert result["by_category"]["paraphrase"]["top3_hit_rate"] == 1.0
        assert abs(result["by_category"]["paraphrase"]["mrr"] - 0.5) < 1e-6

        # Check overall: 2 hits out of 3
        assert abs(result["overall"]["top3_hit_rate"] - 2.0 / 3.0) < 1e-6
        # MRR: (1.0 + 0.0 + 0.5) / 3 = 0.5
        assert abs(result["overall"]["mrr"] - 0.5) < 1e-6

    def test_aggregate_empty_list(self) -> None:
        """Aggregate empty per_case → 0.0 metrics."""
        per_case: list[dict[str, object]] = []
        result = aggregate(per_case)

        assert result["overall"]["top3_hit_rate"] == 0.0
        assert result["overall"]["mrr"] == 0.0


class TestStubEmbedder:
    """Test the StubEmbedder mock."""

    @pytest.mark.asyncio
    async def test_stub_embedder_returns_vector(self) -> None:
        """StubEmbedder.embed returns a list of 8 floats."""
        embedder = StubEmbedder()
        vec = await embedder.embed("hello world")
        assert isinstance(vec, list)
        assert len(vec) == 8
        assert all(isinstance(v, float) for v in vec)
        assert all(0.0 <= v <= 1.0 for v in vec)

    @pytest.mark.asyncio
    async def test_stub_embedder_deterministic(self) -> None:
        """Same text → same embedding."""
        embedder = StubEmbedder()
        vec1 = await embedder.embed("same text")
        vec2 = await embedder.embed("same text")
        assert vec1 == vec2

    @pytest.mark.asyncio
    async def test_stub_embedder_different_text_different_embedding(
        self,
    ) -> None:
        """Different text → different embedding (high probability)."""
        embedder = StubEmbedder()
        vec1 = await embedder.embed("text one")
        vec2 = await embedder.embed("text two")
        # Extremely unlikely to collide
        assert vec1 != vec2


class TestInMemoryStore:
    """Test the InMemoryStore mock."""

    @pytest.mark.asyncio
    async def test_store_clear(self) -> None:
        """Clear removes all items."""
        store = InMemoryStore()
        await store.write("m1", "content", "fact", ["tag1"], [1.0, 0.0])
        assert len(store.items) == 1
        await store.clear()
        assert len(store.items) == 0

    @pytest.mark.asyncio
    async def test_store_write_and_search_basic(self) -> None:
        """Write items, search by similarity."""
        store = InMemoryStore()
        await store.write("m1", "The quick brown fox", "fact", ["animal"], [1.0, 0.0, 0.0])
        await store.write("m2", "The lazy dog", "fact", ["animal"], [0.9, 0.1, 0.0])

        # Query similar to m1
        results = await store.search([1.0, 0.0, 0.0], "quick fox", limit=3)
        assert results[0] == "m1"  # m1 has exact match with query

    @pytest.mark.asyncio
    async def test_store_search_respects_limit(self) -> None:
        """search(limit=k) returns at most k IDs."""
        store = InMemoryStore()
        for i in range(5):
            await store.write(
                f"m{i}",
                f"content {i}",
                "fact",
                [],
                [float(i) / 5.0] * 3,
            )

        results = await store.search([0.5, 0.5, 0.5], "query", limit=2)
        assert len(results) <= 2

    @pytest.mark.asyncio
    async def test_store_search_empty(self) -> None:
        """search on empty store returns empty list."""
        store = InMemoryStore()
        results = await store.search([1.0, 0.0], "query", limit=3)
        assert results == []


class TestRunEvalSmoke:
    """Smoke test for run_eval harness."""

    @pytest.mark.asyncio
    async def test_run_eval_against_mock_store(self) -> None:
        """Load mini dataset, run harness, verify report structure."""
        # Create a tiny inline dataset
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(
                json.dumps(
                    {
                        "id": "case-001",
                        "category": "exact_recall",
                        "state": [
                            {
                                "id": "m1",
                                "content": "PostgreSQL is the database",
                                "type": "decision",
                                "tags": ["db"],
                            },
                            {
                                "id": "m2",
                                "content": "Something else entirely",
                                "type": "fact",
                                "tags": ["other"],
                            },
                        ],
                        "query": "What database do we use?",
                        "expected_top_3_ids": ["m1"],
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "id": "case-002",
                        "category": "paraphrase",
                        "state": [
                            {
                                "id": "m3",
                                "content": "Max size is 10MB per user",
                                "type": "constraint",
                                "tags": ["quota"],
                            },
                            {
                                "id": "m4",
                                "content": "Unrelated content here",
                                "type": "fact",
                                "tags": ["other"],
                            },
                        ],
                        "query": "What is the user size limit?",
                        "expected_top_3_ids": ["m3"],
                    }
                )
                + "\n"
            )
            dataset_path = Path(f.name)

        try:
            embedder = StubEmbedder()
            store = InMemoryStore()

            report = await run_eval(dataset_path, embedder, store)

            # Verify structure
            assert report["n_cases"] == 2
            assert "overall" in report
            assert "by_category" in report
            assert "per_case" in report

            # Verify per_case has correct shape
            assert len(report["per_case"]) == 2
            for case_result in report["per_case"]:
                assert "id" in case_result
                assert "category" in case_result
                assert "top3_hit" in case_result
                assert isinstance(case_result["top3_hit"], bool)
                assert "mrr" in case_result
                assert isinstance(case_result["mrr"], float)
                assert "rank_of_first_expected" in case_result
                assert "retrieved_ids" in case_result
                assert "expected_ids" in case_result

            # Verify overall metrics are in range [0, 1]
            assert 0.0 <= report["overall"]["top3_hit_rate"] <= 1.0
            assert 0.0 <= report["overall"]["mrr"] <= 1.0

        finally:
            dataset_path.unlink()
