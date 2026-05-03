# Retrieval Evaluation Harness

This directory contains the retrieval quality evaluation harness for the memory MCP system (T-9.5, spec §18.5).

## What it measures

The harness measures retrieval quality across two metrics:

- **Top-3 Hit Rate**: percentage of test cases where at least one expected memory appears in the top 3 search results
- **MRR (Mean Reciprocal Rank)**: average of (1 / rank of first expected result), where rank ∈ [1, 3]. Penalizes both misses (0.0) and distant hits

Results are aggregated:
- Overall: across all 20 cases
- By category: separate metrics for exact_recall, paraphrase, recency, supersedence, multi_tag

## Running locally (mock mode)

```bash
python -m evals.retrieval.run_eval \
  --dataset evals/retrieval/dataset.jsonl \
  --output report.json
```

This runs with deterministic stub embeddings and an in-memory vector store. Useful for:
- Local development and validation
- CI/CD pipelines (no DB or external service dependency)
- Validating that the harness itself works

Output: `report.json` with per-case results and aggregates.

## Running against staging DB

*(Not yet wired; deferred to T-1.11 EC2 deploy)*

```bash
python -m evals.retrieval.run_eval \
  --dataset evals/retrieval/dataset.jsonl \
  --output report.json \
  --db-url postgresql://user:pass@staging.example.com/mem_mcp
```

The harness will load the real embedder (Bedrock/Claude API) and staging database. Results go to the same JSON format but reflect live retrieval behavior.

## Dataset format

File: `evals/retrieval/dataset.jsonl`

Each line is a JSON object representing one test case:

```json
{
  "id": "case-001",
  "category": "exact_recall|paraphrase|recency|supersedence|multi_tag",
  "state": [
    {
      "id": "m1",
      "content": "Memory text to embed and store",
      "type": "decision|fact|event|...",
      "tags": ["tag1", "tag2"]
    }
  ],
  "query": "User search query",
  "expected_top_3_ids": ["m1", "m2", "m3"]
}
```

**Categories:**
- `exact_recall`: Query text matches or is very close to memory content
- `paraphrase`: Query rephrases what a memory says (tests semantic understanding)
- `recency`: Multiple memories on same topic; expect newer one first
- `supersedence`: Old version + new version; expect new one (tests overwrite semantics)
- `multi_tag`: Query implies multiple tags; expect all matching memories in top-3

**Dataset:** 20 hand-curated cases, 4 per category. Fictional but realistic scenarios with no PII.

## Interpreting results

### Healthy baseline (mock mode)

On the stub embedder + in-memory cosine similarity:
- **Exact recall**: ~95–100% top-3 hit rate, ~0.95 MRR (deterministic seeding helps)
- **Paraphrase**: ~50–75% top-3 hit rate, ~0.40–0.60 MRR (semantic gap; limited by stub embedder)
- **Recency**: ~75–90% hit rate (insertion order tie-break helps)
- **Supersedence**: ~75–90% hit rate (similar to recency)
- **Multi-tag**: ~60–80% hit rate (depends on embedding quality)
- **Overall**: ~70–80% hit rate, ~0.65–0.75 MRR

When real embeddings (Claude API) + production store are integrated, paraphrase and multi-tag should improve significantly.

### Regression detection

Baseline numbers are recorded in `evals/retrieval/baseline.json` after the first production run. On subsequent runs:
- Compare `report.json` overall metrics to baseline
- A >5% drop in top-3 hit rate or >0.10 drop in MRR indicates a regression
- Per-category metrics can pinpoint which category broke (e.g., recency queries suddenly fail if timestamps aren't indexed)

## Development notes

### Pure metric functions

For unit testing, three functions are exposed:

- `top3_hit(retrieved: list[str], expected: list[str]) -> bool`
- `mrr(retrieved: list[str], expected: list[str]) -> float`
- `aggregate(per_case: list[dict]) -> dict`

These have no dependencies on embeddings, storage, or I/O—easy to test in isolation.

### Protocol seams

The harness uses two Protocol classes for swappable implementations:

- `EmbedderProto`: async method `embed(text: str) -> list[float]`
- `StoreProto`: async methods `clear()`, `write(...)`, `search(...)`

Mock implementations:
- `StubEmbedder`: seeded by SHA256 hash of text
- `InMemoryStore`: cosine similarity with insertion-order tie-breaking

The real implementations (DB connector, Bedrock) are plugged in during production deploy (T-1.11).

### Directory layout

```
evals/
├── __init__.py
├── README.md (this file)
└── retrieval/
    ├── __init__.py
    ├── run_eval.py         # Main harness script + mocks
    ├── dataset.jsonl       # 20 test cases
    └── baseline.json       # Baseline metrics (updated post-deploy)
```

Note: directory is `evals/` (plural) to avoid shadowing the Python `eval` builtin.

## Future work

- **Live integration (T-1.11)**: Wire real DB and Bedrock/Claude API
- **Regression CI gate**: Fail deploy if metrics drop >5%
- **Per-tenant evaluation**: Separate baseline per customer
- **A/B testing**: Compare embedding/indexing strategies
