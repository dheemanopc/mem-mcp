"""Phase 0 evaluation for the Space Vocabulary spec (memsys memo a12959d7).

Decision rule from the spec: implementation proceeds ONLY if this gate
passes. Compares three retrieval configurations over real queries:

  A. Current: direct vector search over memories.embedding (Titan v2 —
     the corpus is NOT re-embedded; prod vectors are used as-is).
  B. Vocabulary route: query → cosine vs per-space vocabulary phrases →
     top-k phrases (>= similarity floor, capped) → OR-semantics
     websearch_to_tsquery over content_tsv → ts_rank_cd ranking.
  C. Union: reciprocal-rank fusion of A and B (the spec says "union";
     RRF is the parameter-free fusion used here — noted deviation).

Metrics per config: hit@5, MRR, token cost of the top-5 returned bodies
(chars/4), mean latency. Viability gate (from the spec): B wins or ties A
on hit@5 while materially cheaper in tokens, OR C beats A meaningfully.

Usage (on a box with DB + Bedrock access — e.g. prod host):

    MEM_MCP_... poetry run python -m evals.vocab_route.run_eval \
        --dsn "$MEM_MCP_DB_MAINT_DSN" \
        --tenant-id <tenant-uuid> \
        --dataset evals/vocab_route/dataset.jsonl \
        --spaces evals/vocab_route/spaces.json \
        --output vocab_route_report.json

Without --dsn it runs in mock mode (stub embedder + in-memory corpus from
--corpus) — validates the harness plumbing in CI, NOT the viability gate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any, Protocol

import structlog

from evals.vocab_route.phrases import build_vocabulary

logger = structlog.get_logger(__name__)

VOCAB_MAX_SIZE = 300  # per space, before embedding (spec open question — v0 cap)
VOCAB_DEDUP_COSINE = 0.93  # spec: dedup-on-insert threshold
ROUTE_SIM_FLOOR = 0.75  # spec: harvest keywords above ~0.75
ROUTE_MAX_PHRASES = 8  # spec: cap ~8-10
TOP_K = 5
RRF_K = 60


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def hit_at_5(retrieved: list[str], expected: list[str]) -> bool:
    return any(e in retrieved[:TOP_K] for e in expected)


def mrr(retrieved: list[str], expected: list[str]) -> float:
    for rank, rid in enumerate(retrieved, start=1):
        if rid in expected:
            return 1.0 / rank
    return 0.0


def rrf_fuse(ranking_a: list[str], ranking_b: list[str]) -> list[str]:
    """Reciprocal-rank fusion of two rankings."""
    scores: dict[str, float] = {}
    for ranking in (ranking_a, ranking_b):
        for rank, rid in enumerate(ranking, start=1):
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores, key=lambda r: -scores[r])


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Embedder boundary (real = Bedrock via factory; mock = hash stub)
# ---------------------------------------------------------------------------


class Embedder(Protocol):
    async def embed(self, text: str) -> Any: ...  # returns .vector


class StubEmbedder:
    """Deterministic hash-based vectors — plumbing validation only."""

    DIM = 64

    async def embed(self, text: str) -> Any:
        h = hashlib.sha256(text.lower().encode()).digest()
        vec = [(h[i % 32] / 255.0) - 0.5 for i in range(self.DIM)]

        class _R:
            vector = vec
            input_tokens = max(1, len(text) // 4)

        return _R()


# ---------------------------------------------------------------------------
# Vocabulary build (shared by both backends)
# ---------------------------------------------------------------------------


async def build_space_vocab(
    embedder: Embedder, documents: list[str]
) -> list[tuple[str, int, list[float]]]:
    """Extract + embed + cosine-dedup the space vocabulary.

    Returns [(phrase, frequency, vector)] — dedup keeps the higher-frequency
    entry per the spec's dedup-on-insert rule.
    """
    ranked = build_vocabulary(documents, max_size=VOCAB_MAX_SIZE)
    entries: list[tuple[str, int, list[float]]] = []
    for phrase, freq in ranked:
        vec = (await embedder.embed(phrase)).vector
        entries.append((phrase, freq, vec))

    kept: list[tuple[str, int, list[float]]] = []
    for phrase, freq, vec in entries:  # already frequency-descending
        if any(cosine(vec, kv) >= VOCAB_DEDUP_COSINE for _, _, kv in kept):
            continue
        kept.append((phrase, freq, vec))
    return kept


def route_phrases(
    query_vec: list[float], vocab: list[tuple[str, int, list[float]]]
) -> list[tuple[str, float]]:
    """Stage 1 of config B: top phrases by cosine vs the query, floored + capped."""
    scored = [(phrase, cosine(query_vec, vec)) for phrase, _, vec in vocab]
    scored = [(p, s) for p, s in scored if s >= ROUTE_SIM_FLOOR]
    scored.sort(key=lambda ps: -ps[1])
    return scored[:ROUTE_MAX_PHRASES]


# ---------------------------------------------------------------------------
# DB backend (real run)
# ---------------------------------------------------------------------------

_CORPUS_SQL = """
SELECT id::text, content
FROM memories
WHERE tenant_id = $1 AND deleted_at IS NULL AND is_current = true
  AND indexable = true AND tags && $2::text[]
"""

_VECTOR_SEARCH_SQL = """
SELECT id::text, content
FROM memories
WHERE tenant_id = $1 AND deleted_at IS NULL AND is_current = true
  AND indexable = true AND embedding IS NOT NULL AND tags && $2::text[]
ORDER BY embedding <=> $3::vector
LIMIT $4
"""

_TSQUERY_SEARCH_SQL = """
SELECT id::text, content, ts_rank_cd(content_tsv, q) AS rank
FROM memories, websearch_to_tsquery('english', $3) q
WHERE tenant_id = $1 AND deleted_at IS NULL AND is_current = true
  AND indexable = true AND tags && $2::text[]
  AND content_tsv @@ q
ORDER BY rank DESC
LIMIT $4
"""


class DbBackend:
    def __init__(self, conn: Any, tenant_id: str) -> None:
        self.conn = conn
        self.tenant_id = tenant_id

    async def corpus(self, space_tags: list[str]) -> list[dict[str, str]]:
        rows = await self.conn.fetch(_CORPUS_SQL, self.tenant_id, space_tags)
        return [dict(r) for r in rows]

    async def vector_search(
        self, space_tags: list[str], query_vec: list[float], k: int
    ) -> list[dict[str, str]]:
        vec_literal = "[" + ",".join(f"{x:.8f}" for x in query_vec) + "]"
        rows = await self.conn.fetch(_VECTOR_SEARCH_SQL, self.tenant_id, space_tags, vec_literal, k)
        return [dict(r) for r in rows]

    async def keyword_search(
        self, space_tags: list[str], phrases: list[str], k: int
    ) -> list[dict[str, str]]:
        if not phrases:
            return []
        websearch = " OR ".join(f'"{p}"' for p in phrases)
        rows = await self.conn.fetch(_TSQUERY_SEARCH_SQL, self.tenant_id, space_tags, websearch, k)
        return [{"id": r["id"], "content": r["content"]} for r in rows]


class MockBackend:
    """In-memory corpus; vector = stub cosine, keyword = phrase containment."""

    def __init__(self, corpus: list[dict[str, Any]], embedder: Embedder) -> None:
        self._corpus = corpus
        self._embedder = embedder
        self._vecs: dict[str, list[float]] = {}

    async def _vec(self, doc: dict[str, Any]) -> list[float]:
        if doc["id"] not in self._vecs:
            self._vecs[doc["id"]] = (await self._embedder.embed(doc["content"])).vector
        return self._vecs[doc["id"]]

    def _in_space(self, doc: dict[str, Any], space_tags: list[str]) -> bool:
        return bool(set(doc.get("tags", [])) & set(space_tags))

    async def corpus(self, space_tags: list[str]) -> list[dict[str, str]]:
        return [d for d in self._corpus if self._in_space(d, space_tags)]

    async def vector_search(
        self, space_tags: list[str], query_vec: list[float], k: int
    ) -> list[dict[str, str]]:
        docs = await self.corpus(space_tags)
        scored = [(cosine(query_vec, await self._vec(d)), d) for d in docs]
        scored.sort(key=lambda sd: -sd[0])
        return [d for _, d in scored[:k]]

    async def keyword_search(
        self, space_tags: list[str], phrases: list[str], k: int
    ) -> list[dict[str, str]]:
        docs = await self.corpus(space_tags)
        scored = [(sum(d["content"].lower().count(p) for p in phrases), d) for d in docs]
        scored = [(s, d) for s, d in scored if s > 0]
        scored.sort(key=lambda sd: -sd[0])
        return [d for _, d in scored[:k]]


# ---------------------------------------------------------------------------
# Evaluation loop
# ---------------------------------------------------------------------------


def _tokens(results: list[dict[str, str]]) -> int:
    return sum(len(r["content"]) // 4 for r in results[:TOP_K])


async def run_eval(
    backend: Any,
    embedder: Embedder,
    cases: list[dict[str, Any]],
    spaces: dict[str, list[str]],
) -> dict[str, Any]:
    # One vocabulary per space, built from that space's corpus.
    vocabs: dict[str, list[tuple[str, int, list[float]]]] = {}
    for space, tags in spaces.items():
        docs = await backend.corpus(tags)
        vocabs[space] = await build_space_vocab(embedder, [d["content"] for d in docs])
        logger.info("space_vocab_built", space=space, corpus=len(docs), vocab=len(vocabs[space]))

    per_case: list[dict[str, Any]] = []
    for case in cases:
        space = case["space"]
        tags = spaces[space]
        expected = case["expected_ids"]
        qvec = (await embedder.embed(case["query"])).vector

        t0 = time.monotonic()
        results_a = await backend.vector_search(tags, qvec, TOP_K * 2)
        lat_a = time.monotonic() - t0

        routed = route_phrases(qvec, vocabs[space])
        t0 = time.monotonic()
        results_b = await backend.keyword_search(tags, [p for p, _ in routed], TOP_K * 2)
        lat_b = time.monotonic() - t0
        # Spec: mandatory fallback when the route is thin.
        b_fell_back = len(results_b) < 3
        if b_fell_back:
            results_b = results_a

        ids_a = [r["id"] for r in results_a]
        ids_b = [r["id"] for r in results_b]
        ids_c = rrf_fuse(ids_a, ids_b)

        per_case.append(
            {
                "id": case["id"],
                "space": space,
                "routed_phrases": [p for p, _ in routed],
                "b_fell_back": b_fell_back,
                "A": {
                    "hit@5": hit_at_5(ids_a, expected),
                    "mrr": mrr(ids_a, expected),
                    "tokens_top5": _tokens(results_a),
                    "latency_ms": round(lat_a * 1000, 1),
                },
                "B": {
                    "hit@5": hit_at_5(ids_b, expected),
                    "mrr": mrr(ids_b, expected),
                    "tokens_top5": _tokens(results_b),
                    "latency_ms": round(lat_b * 1000, 1),
                },
                "C": {"hit@5": hit_at_5(ids_c, expected), "mrr": mrr(ids_c, expected)},
            }
        )

    def _agg(config: str, key: str) -> float:
        vals = [float(c[config][key]) for c in per_case if key in c[config]]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    summary = {
        config: {
            "hit@5": _agg(config, "hit@5"),
            "mrr": _agg(config, "mrr"),
            **(
                {
                    "tokens_top5": _agg(config, "tokens_top5"),
                    "latency_ms": _agg(config, "latency_ms"),
                }
                if config in ("A", "B")
                else {}
            ),
        }
        for config in ("A", "B", "C")
    }
    gate = (
        summary["B"]["hit@5"] >= summary["A"]["hit@5"]
        and summary["B"].get("tokens_top5", 0) < summary["A"].get("tokens_top5", 1)
    ) or summary["C"]["hit@5"] > summary["A"]["hit@5"]

    return {
        "n_cases": len(per_case),
        "summary": summary,
        "viability_gate_passed": gate,
        "per_case": per_case,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def _amain(args: argparse.Namespace) -> int:
    cases = _load_jsonl(Path(args.dataset))
    spaces = json.loads(Path(args.spaces).read_text())

    if args.dsn:
        import asyncpg  # type: ignore[import-untyped]

        from mem_mcp.config import get_settings
        from mem_mcp.embeddings.factory import make_embedding_client

        embedder: Embedder = make_embedding_client(get_settings())
        conn = await asyncpg.connect(dsn=args.dsn)
        try:
            backend: Any = DbBackend(conn, args.tenant_id)
            report = await run_eval(backend, embedder, cases, spaces)
        finally:
            await conn.close()
    else:
        if not args.corpus:
            raise SystemExit("mock mode requires --corpus")
        embedder = StubEmbedder()
        backend = MockBackend(_load_jsonl(Path(args.corpus)), embedder)
        report = await run_eval(backend, embedder, cases, spaces)
        report["mode"] = "mock (stub embedder — NOT the viability gate)"

    Path(args.output).write_text(json.dumps(report, indent=2))
    logger.info(
        "eval_complete",
        output=args.output,
        summary=report["summary"],
        gate=report["viability_gate_passed"],
    )
    print(json.dumps(report["summary"], indent=2))
    print(f"viability_gate_passed: {report['viability_gate_passed']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Space-vocabulary Phase 0 eval")
    parser.add_argument("--dataset", default="evals/vocab_route/dataset.jsonl")
    parser.add_argument("--spaces", default="evals/vocab_route/spaces.json")
    parser.add_argument("--dsn", default=None, help="DB DSN (omit for mock mode)")
    parser.add_argument("--tenant-id", default=None, help="tenant UUID (DB mode)")
    parser.add_argument("--corpus", default=None, help="corpus JSONL (mock mode)")
    parser.add_argument("--output", default="vocab_route_report.json")
    args = parser.parse_args()
    if args.dsn and not args.tenant_id:
        raise SystemExit("--tenant-id required with --dsn")
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
