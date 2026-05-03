"""Retrieval evaluation harness (T-9.5, spec §18.5).

Usage:
    python -m evals.retrieval.run_eval --dataset evals/retrieval/dataset.jsonl --output report.json

Without --db-url, runs in 'mock' mode using a stub embedder + in-memory store
(useful for unit tests + CI validation that the harness itself works).

Dataset format: JSONL, each line:
    {
      "id": "case-001",
      "category": "exact_recall|paraphrase|recency|supersedence|multi_tag",
      "state": [
        {"id": "m1", "content": "...", "type": "...", "tags": [...]},
        ...
      ],
      "query": "user query string",
      "expected_top_3_ids": ["m1", "m2", "m3"]   # ranked
    }

Output: JSON with shape:
    {
      "dataset": "evals/retrieval/dataset.jsonl",
      "n_cases": 20,
      "by_category": { "exact_recall": {"top3_hit_rate": 0.83, "mrr": 0.71}, ... },
      "overall": { "top3_hit_rate": 0.78, "mrr": 0.65 },
      "per_case": [ { "id": "case-001", "top3_hit": true, "rank_of_first_expected": 1, ...}, ... ]
    }
"""

import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)


# Pure functions for metric computation
def top3_hit(retrieved: list[str], expected: list[str]) -> bool:
    """True if any expected ID appears in the first 3 retrieved IDs."""
    return any(exp_id in retrieved[:3] for exp_id in expected)


def mrr(retrieved: list[str], expected: list[str]) -> float:
    """Reciprocal rank of the first expected ID in the retrieved list.

    Returns 0.0 if not found.
    """
    for rank, ret_id in enumerate(retrieved, start=1):
        if ret_id in expected:
            return 1.0 / rank
    return 0.0


def aggregate(per_case: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute by_category + overall aggregate from per_case results."""
    by_category: dict[str, dict[str, Any]] = {}
    overall_hits = 0
    overall_mrr_sum = 0.0
    total_cases = len(per_case)

    for case in per_case:
        category = case["category"]
        top3_hit_val = case["top3_hit"]
        mrr_val = case["mrr"]

        if category not in by_category:
            by_category[category] = {
                "top3_hit_rate": 0.0,
                "mrr": 0.0,
                "n_cases": 0,
                "_hits": 0,
                "_mrr_sum": 0.0,
            }

        by_category[category]["_hits"] += 1 if top3_hit_val else 0
        by_category[category]["_mrr_sum"] += mrr_val
        by_category[category]["n_cases"] += 1

        overall_hits += 1 if top3_hit_val else 0
        overall_mrr_sum += mrr_val

    # Finalize by_category
    for category in by_category:
        n = by_category[category]["n_cases"]
        by_category[category]["top3_hit_rate"] = (
            by_category[category]["_hits"] / n if n > 0 else 0.0
        )
        by_category[category]["mrr"] = by_category[category]["_mrr_sum"] / n if n > 0 else 0.0
        # Remove temp fields
        del by_category[category]["_hits"]
        del by_category[category]["_mrr_sum"]

    return {
        "by_category": by_category,
        "overall": {
            "top3_hit_rate": (overall_hits / total_cases) if total_cases > 0 else 0.0,
            "mrr": (overall_mrr_sum / total_cases) if total_cases > 0 else 0.0,
        },
    }


# Protocol seams for embedding and storage
class EmbedderProto(Protocol):
    """Protocol for embedding text -> vector."""

    async def embed(self, text: str) -> list[float]:
        """Embed text into a vector."""
        ...


class StoreProto(Protocol):
    """Protocol for in-memory or persistent vector store."""

    async def clear(self) -> None:
        """Clear all items from the store."""
        ...

    async def write(
        self, mem_id: str, content: str, type_: str, tags: list[str], embedding: list[float]
    ) -> None:
        """Write a memory item with its embedding."""
        ...

    async def search(
        self, query_embedding: list[float], query_text: str, limit: int = 3
    ) -> list[str]:
        """Search and return top-k memory IDs (ordered by relevance)."""
        ...


# Mock implementations
class StubEmbedder:
    """Deterministic embedder: seeded by SHA256 hash of text."""

    async def embed(self, text: str) -> list[float]:
        """Return a deterministic 8-dim vector seeded by text hash."""
        h = hashlib.sha256(text.encode("utf-8")).digest()
        # Convert bytes to 8 floats in range [0, 1]
        values = []
        for i in range(8):
            byte_val = h[i * 4 % len(h)]
            values.append((byte_val % 256) / 256.0)
        return values


class InMemoryStore:
    """In-memory vector store using cosine similarity."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.embeddings: dict[str, list[float]] = {}
        self.insertion_order: list[str] = []

    async def clear(self) -> None:
        """Clear all items."""
        self.items.clear()
        self.embeddings.clear()
        self.insertion_order.clear()

    async def write(
        self, mem_id: str, content: str, type_: str, tags: list[str], embedding: list[float]
    ) -> None:
        """Write a memory item."""
        self.items[mem_id] = {
            "id": mem_id,
            "content": content,
            "type": type_,
            "tags": tags,
        }
        self.embeddings[mem_id] = embedding
        if mem_id not in self.insertion_order:
            self.insertion_order.append(mem_id)

    async def search(
        self, query_embedding: list[float], query_text: str, limit: int = 3
    ) -> list[str]:
        """Search using cosine similarity; ties broken by insertion order."""
        scores: list[tuple[float, int, str]] = []

        for mem_id, embedding in self.embeddings.items():
            sim = self._cosine_similarity(query_embedding, embedding)
            insertion_idx = (
                self.insertion_order.index(mem_id) if mem_id in self.insertion_order else 999999
            )
            scores.append((sim, insertion_idx, mem_id))

        # Sort by similarity (desc), then insertion order (asc)
        scores.sort(key=lambda x: (-x[0], x[1]))
        return [mem_id for _, _, mem_id in scores[:limit]]

    @staticmethod
    def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not vec1 or not vec2:
            return 0.0
        dot_product = sum(a * b for a, b in zip(vec1, vec2, strict=False))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot_product / (norm1 * norm2)


# Async harness
async def run_eval(
    dataset_path: Path,
    embedder: EmbedderProto,
    store: StoreProto,
) -> dict[str, Any]:
    """Load dataset, for each case: clear store, write state items, run search, score.

    Returns EvalReport dict.
    """
    # Load dataset
    cases = []
    with open(dataset_path) as f:
        for line in f:
            if line.strip():
                cases.append(json.loads(line))

    per_case_results = []

    for case in cases:
        case_id = case["id"]
        category = case["category"]
        state = case["state"]
        query = case["query"]
        expected_ids = case["expected_top_3_ids"]

        # Clear store for fresh test
        await store.clear()

        # Write state items with embeddings
        for item in state:
            embedding = await embedder.embed(item["content"])
            await store.write(
                mem_id=item["id"],
                content=item["content"],
                type_=item["type"],
                tags=item.get("tags", []),
                embedding=embedding,
            )

        # Run search on query
        query_embedding = await embedder.embed(query)
        retrieved_ids = await store.search(query_embedding, query, limit=3)

        # Score
        top3_hit_val = top3_hit(retrieved_ids, expected_ids)
        mrr_val = mrr(retrieved_ids, expected_ids)

        # Determine rank of first expected ID
        rank_of_first = None
        for rank, ret_id in enumerate(retrieved_ids, start=1):
            if ret_id in expected_ids:
                rank_of_first = rank
                break

        per_case_results.append(
            {
                "id": case_id,
                "category": category,
                "top3_hit": top3_hit_val,
                "mrr": mrr_val,
                "rank_of_first_expected": rank_of_first,
                "retrieved_ids": retrieved_ids,
                "expected_ids": expected_ids,
            }
        )

    # Aggregate
    agg = aggregate(per_case_results)

    return {
        "dataset": str(dataset_path),
        "n_cases": len(cases),
        "by_category": agg["by_category"],
        "overall": agg["overall"],
        "per_case": per_case_results,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Parse args, load dataset, run, write JSON output."""
    parser = argparse.ArgumentParser(description="Retrieval evaluation harness (T-9.5)")
    parser.add_argument(
        "--dataset",
        required=True,
        type=Path,
        help="Path to JSONL dataset",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path to write JSON report",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="(Future) DB URL for live integration; not yet implemented",
    )

    args = parser.parse_args(argv)

    if args.db_url:
        logger.info("real DB mode not yet wired; use --mock", db_url=args.db_url)
        return 0

    # Run in mock mode
    embedder = StubEmbedder()
    store = InMemoryStore()

    report = asyncio.run(run_eval(args.dataset, embedder, store))

    # Write report
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2)

    logger.info("eval complete", output=str(args.output), n_cases=report["n_cases"])
    return 0


if __name__ == "__main__":
    exit(main())
