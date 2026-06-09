# MYPLAN.md

## Feature: Contradiction Detection on Write

### Problem
When an AI client writes a new memory, it may contradict an existing one (e.g. "we use PostgreSQL" vs "we migrated to MySQL"). The current deduplication only catches near-identical content. Contradictions are stored silently and poison future search results.

**Why not cosine similarity?** Cosine similarity measures topic proximity, not factual opposition. "We use PostgreSQL" and "PostgreSQL supports JSONB" both score >0.75 against a new Postgres memory — they're related, not contradictions. Using cosine thresholds produces high false-positive rates that destroy client trust in the signal.

**Chosen mechanism: NLI via Ollama.** A small generative model in the existing Ollama instance classifies a pair of memories as CONTRADICTION / NEUTRAL / ENTAILMENT using a structured prompt — no new infrastructure, zero cost when no candidates exist.

### Proposed tool behaviour
`memory_write` gains an optional `check_contradictions: bool = False` parameter. When true, the service fetches the N most recent memories of the same type (recency window, default 20), scores each pair via Ollama NLI, and returns genuine contradiction candidates in the response. The write always proceeds — advisory only.

### API change (non-breaking)

**Request addition to `MemoryWriteInput`:**
```python
check_contradictions: bool = False
contradiction_limit: int = 3   # max candidates to return
```
Note: `contradiction_threshold` is server-side config (`MEM_MCP_NLI_CONTRADICTION_THRESHOLD`), not a per-call parameter.

**Response addition to `MemoryWriteOutput`:**
```python
contradictions: list[ContradictionCandidate] | None = None
```

```python
@dataclass(frozen=True)
class ContradictionCandidate:
    memory_id: UUID
    content_snippet: str      # first 200 chars
    contradiction_score: float  # 0.0–1.0, NLI model confidence
    type: str
    tags: list[str]
    created_at: datetime
```

**New settings (`src/mem_mcp/settings.py`):**
```python
nli_backend: Literal["none", "ollama"] = "none"
nli_ollama_model: str = "llama3.2:1b"
nli_contradiction_threshold: float = 0.7
nli_candidate_window: int = 20
```
When `nli_backend="none"` (default), `check_contradictions=True` returns `contradictions: []` — no-op without server config.

### Implementation plan

1. **`src/mem_mcp/memory/contradiction.py`** — new module  
   - `find_contradiction_candidates(conn, tenant_id, new_content, type_, ollama_url, model, threshold, limit, window)` → `list[ContradictionCandidate]`  
   - SQL: recency window — `ORDER BY created_at DESC LIMIT window` (no cosine, no pgvector)  
   - For each candidate: call Ollama NLI prompt, parse `CONTRADICTION <score>` response, keep if `score >= threshold`  
   - On Ollama failure: log warning, return `[]` — never fail the write

2. **`src/mem_mcp/settings.py`** — add NLI config  
   - `nli_backend`, `nli_ollama_model`, `nli_contradiction_threshold`, `nli_candidate_window`

3. **`src/mem_mcp/mcp/tools/write.py`** — add `check_contradictions` param  
   - Declare `contradictions_result: list[ContradictionCandidate] | None = None` **before** `tenant_tx`  
   - Inside `tenant_tx`, before `check_dup`: if `check_contradictions=True`, set `contradictions_result` (empty list if `nli_backend=none`, NLI results otherwise)  
   - Pass `contradictions=contradictions_result` in **all four** `MemoryWriteOutput(...)` return statements: dedupe early return, reply early return, supersede early return, and plain INSERT  
   - Does not block the write — always proceeds, always returns the memory id

4. **`src/mem_mcp/mcp/tool_descriptions.py`** — update `memory_write` description

5. **`tests/unit/test_contradiction.py`** — unit tests for NLI prompt parsing + `find_contradiction_candidates`

6. **`tests/integration/test_write_contradiction.py`** — integration test: write "we use Postgres", write "we use MySQL" with `check_contradictions=True` (mock Ollama), assert first memory appears in `contradictions`

### What's NOT in scope (v1)
- Cosine similarity as contradiction detection (replaced by NLI — high false-positive rate for factual opposition)
- sentence-transformers / NLI cross-encoder in-process (Ollama only for v1)
- Keyword+recency hybrid candidate selection — consider Phase 2 if recency window misses older contradictions
- Auto-supersede on contradiction
- Cross-team contradiction detection
- Background contradiction scan over existing memories

---

## Detailed Implementation Plan

Ordered by dependency. Each step has an exact file, what to change, and the code to add/modify.

---

### Step 1 — `src/mem_mcp/config.py`: Add NLI settings

Add 4 fields to `Settings` (after `ollama_embed_model: str = "bge-m3"`, line 83):

```python
# NLI contradiction detection via Ollama generative model
nli_backend: str = "none"              # "none" | "ollama"
nli_ollama_model: str = "llama3.2:1b"
nli_contradiction_threshold: float = 0.7  # confidence floor for CONTRADICTION label
nli_candidate_window: int = 20            # recent memories of same type to compare
```

Env vars (auto-wired by pydantic-settings prefix `MEM_MCP_`):
`MEM_MCP_NLI_BACKEND`, `MEM_MCP_NLI_OLLAMA_MODEL`, `MEM_MCP_NLI_CONTRADICTION_THRESHOLD`, `MEM_MCP_NLI_CANDIDATE_WINDOW`

**Verify:** `get_settings()` cache returns the new fields; `_reset_settings_cache_for_tests()` clears them.

---

### Step 2 — `src/mem_mcp/memory/contradiction.py`: New module

Full file:

```python
"""Contradiction detection via Ollama NLI (Natural Language Inference).

Detection flow:
1. Fetch the N most recent memories of the same type (recency window).
2. For each, call Ollama /api/generate with a structured classification prompt.
3. Parse label (CONTRADICTION/NEUTRAL/ENTAILMENT) + confidence score.
4. Return candidates where contradiction_score >= threshold, up to limit.

On any Ollama failure the error is logged and that candidate is skipped —
the write always proceeds regardless.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

NLI_DEFAULT_THRESHOLD = 0.7
NLI_DEFAULT_LIMIT = 3
NLI_DEFAULT_CANDIDATE_WINDOW = 20
NLI_DEFAULT_MODEL = "llama3.2:1b"

_NLI_PROMPT = """\
Memory A: {a}
Memory B: {b}

Do Memory A and Memory B state contradictory facts? Consider whether they make \
conflicting claims about the same topic.

Reply with exactly one of:
CONTRADICTION <score>   (conflicting facts, score = your confidence 0.0-1.0)
NEUTRAL <score>         (compatible or unrelated)
ENTAILMENT <score>      (one supports the other)

Example: CONTRADICTION 0.85"""

_NLI_RE = re.compile(r"(CONTRADICTION|NEUTRAL|ENTAILMENT)\s+([\d.]+)", re.IGNORECASE)


@dataclass(frozen=True)
class ContradictionCandidate:
    memory_id: UUID
    content_snippet: str       # first 200 chars
    contradiction_score: float  # 0.0-1.0, NLI model confidence
    type: str
    tags: list[str]
    created_at: datetime


def _parse_nli_response(text: str) -> tuple[str, float]:
    """Parse 'CONTRADICTION 0.85' → ('CONTRADICTION', 0.85). Defaults NEUTRAL 0.0."""
    m = _NLI_RE.search(text.strip())
    if m:
        label = m.group(1).upper()
        score = min(1.0, max(0.0, float(m.group(2))))
        return label, score
    return "NEUTRAL", 0.0


async def _score_contradiction(a: str, b: str, ollama_url: str, model: str) -> float:
    """Call Ollama /api/generate; return contradiction confidence 0.0-1.0."""
    import httpx

    prompt = _NLI_PROMPT.format(a=a[:500], b=b[:500])
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{ollama_url.rstrip('/')}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
        )
        resp.raise_for_status()
        data = resp.json()

    raw = data.get("response", "")
    label, score = _parse_nli_response(raw)
    return score if label == "CONTRADICTION" else 0.0


async def find_contradiction_candidates(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    new_content: str,
    type_: str,
    ollama_url: str,
    model: str = NLI_DEFAULT_MODEL,
    threshold: float = NLI_DEFAULT_THRESHOLD,
    limit: int = NLI_DEFAULT_LIMIT,
    window: int = NLI_DEFAULT_CANDIDATE_WINDOW,
) -> list[ContradictionCandidate]:
    """Fetch recent same-type memories, score via NLI, return contradictions."""
    rows = await conn.fetch(
        """
        SELECT id, content, type, tags, created_at
        FROM memories
        WHERE tenant_id = $1 AND type = $2
          AND deleted_at IS NULL AND is_current = true
        ORDER BY created_at DESC
        LIMIT $3
        """,
        tenant_id,
        type_,
        window,
    )

    results: list[ContradictionCandidate] = []
    for row in rows:
        try:
            score = await _score_contradiction(new_content, row["content"], ollama_url, model)
        except Exception as exc:
            logger.warning("NLI scoring failed for candidate %s: %s", row["id"], exc)
            continue
        if score >= threshold:
            results.append(
                ContradictionCandidate(
                    memory_id=row["id"],
                    content_snippet=row["content"][:200],
                    contradiction_score=score,
                    type=row["type"],
                    tags=list(row["tags"] or []),
                    created_at=row["created_at"],
                )
            )

    results.sort(key=lambda c: c.contradiction_score, reverse=True)
    return results[:limit]
```

---

### Step 3 — `src/mem_mcp/mcp/tools/write.py`: Wire contradiction check

**3a. Imports** — add at the top (after existing imports):
```python
from mem_mcp.config import get_settings
from mem_mcp.memory.contradiction import ContradictionCandidate, find_contradiction_candidates
```

**3b. `MemoryWriteInput`** — add two fields after `fragment_id: int | None = None` (line 89):
```python
check_contradictions: bool = False
contradiction_limit: int = Field(default=3, ge=1, le=10)
```

**3c. `MemoryWriteOutput`** — add one field after `embedding_status: str` (line 197):
```python
contradictions: list[ContradictionCandidate] | None = None
```

**3d. In `__call__`** — add before `async with tenant_tx(...)` (line 248), after `embed_or_skip` block:
```python
# Initialize before tenant_tx so all four return paths always see it
contradictions_result: list[ContradictionCandidate] | None = None
s = get_settings()
_nli_enabled = (
    inp.check_contradictions
    and s.nli_backend == "ollama"
    and s.ollama_url is not None
)
```

**3e.** — add inside `tenant_tx`, after reference resolution (after line 316, before `# Dedupe` comment):
```python
            # Contradiction detection (NLI via Ollama) — advisory, never blocking
            if inp.check_contradictions:
                if _nli_enabled:
                    contradictions_result = await find_contradiction_candidates(
                        conn,
                        ctx.tenant_id,
                        inp.content,
                        inp.type,
                        ollama_url=s.ollama_url,  # type: ignore[arg-type]
                        model=s.nli_ollama_model,
                        threshold=s.nli_contradiction_threshold,
                        limit=inp.contradiction_limit,
                        window=s.nli_candidate_window,
                    )
                else:
                    contradictions_result = []
```

**3f. All four `return MemoryWriteOutput(...)` calls** — add `contradictions=contradictions_result` to each:

| Line | Current last arg | Add |
|------|-----------------|-----|
| ~360 (dedupe) | `embedding_status=embedding_status,` | `contradictions=contradictions_result,` |
| ~492 (reply) | `embedding_status=embedding_status,` | `contradictions=contradictions_result,` |
| ~620 (supersede) | `embedding_status=embedding_status,` | `contradictions=contradictions_result,` |
| ~719 (plain INSERT) | `embedding_status=embedding_status,` | `contradictions=contradictions_result,` |

---

### Step 4 — `src/mem_mcp/mcp/tool_descriptions.py`: Update description

Append to the `memory_write` description string (after the last `Note:` sentence):

```
Contradiction detection: pass `check_contradictions=true` to scan recent memories of the same type for factual conflicts using NLI (Natural Language Inference). Returns `contradictions: [{memory_id, content_snippet, contradiction_score, type, tags, created_at}]` sorted by confidence descending, up to `contradiction_limit` (default 3). The write always proceeds — contradictions are advisory. Requires `MEM_MCP_NLI_BACKEND=ollama` server-side configuration; returns `[]` when NLI is not configured.
```

---

### Step 5 — Tests

**`tests/unit/test_contradiction.py`** — 8 tests covering:
1. `_parse_nli_response("CONTRADICTION 0.9")` → `("CONTRADICTION", 0.9)`
2. `_parse_nli_response("NEUTRAL 0.1")` → `("NEUTRAL", 0.1)`
3. `_parse_nli_response("garbage")` → `("NEUTRAL", 0.0)`
4. `_parse_nli_response("CONTRADICTION 1.5")` → score clamped to 1.0
5. `find_contradiction_candidates` with mock conn (0 rows) → `[]`
6. `find_contradiction_candidates` with mock conn (1 row) + mock `_score_contradiction` returning 0.85 → 1 result
7. `find_contradiction_candidates` score below threshold → `[]`
8. `find_contradiction_candidates` Ollama raises → warning logged, `[]` returned

**`tests/integration/test_write_contradiction.py`** — 3 tests:
1. `nli_backend=none`: `check_contradictions=True` → `contradictions: []`, no Ollama call
2. `nli_backend=ollama` (mock Ollama returning `CONTRADICTION 0.9`): write "we use Postgres", write "we use MySQL" with `check_contradictions=True` → first memory in `contradictions`
3. Ollama timeout → write succeeds, `contradictions: []`

---

### Execution order

```
1. config.py      (settings — no deps)
2. contradiction.py  (new module — no write.py dep)
3. write.py          (wires config + contradiction)
4. tool_descriptions.py  (string change — last)
5. tests/unit/        (mock-only, fast)
6. tests/integration/ (needs DB + mock Ollama)
```

**Total estimate: ~5.75h** (matches issue #315)

---

## Bugs Found (Code Review — HEAD~10..HEAD)

All bugs confirmed via independent verification agents. Ranked by severity.

---

### BUG-1 (Security) — Scope enforcement disabled by COGNITO_ISSUER_URL
**File:** [src/mem_mcp/mcp/registry.py](src/mem_mcp/mcp/registry.py) line 146  
**Severity:** Critical  
**Status:** CONFIRMED

`ToolRegistry(skip_scope_check=bool(s.cognito_issuer_url))` in `main.py` means setting `MEM_MCP_COGNITO_ISSUER_URL` in *any* environment (staging, misconfigured prod) silently disables all per-tool OAuth scope enforcement. Every authenticated caller can invoke `memory.write` tools regardless of whether their token carries the `memory.write` scope.

**Fix:** Decouple the two concerns. Add a separate `MEM_MCP_SKIP_SCOPE_CHECK=true` env var (default false, only valid with `MEM_MCP_COGNITO_ISSUER_URL` also set). Do not derive `skip_scope_check` from `cognito_issuer_url` alone.

---

### BUG-2 (Security) — NULL team_id grants universal access via can_access_team_resource
**File:** [src/mem_mcp/teams/references.py](src/mem_mcp/teams/references.py) line 118  
**Severity:** High  
**Status:** CONFIRMED

The SQL function `can_access_team_resource` returns `TRUE` when `p_resource_team IS NULL` (first clause, migration 0035). If a memory row has `team_id = NULL` (pre-migration-0026 backfill failure), any authenticated caller can reference or read it — no team membership check is performed.

**Fix:** Add a NULL guard in `resolve_reference_target` before calling `can_access_team_resource`. If `row["team_id"] IS NULL`, treat as inaccessible (or restrict to same-tenant only).

---

### BUG-3 (Security) — Hard-deleted source memories leak as accessible citers in refs_in
**File:** [src/mem_mcp/mcp/tools/refs_in.py](src/mem_mcp/mcp/tools/refs_in.py) line 59  
**Severity:** High  
**Status:** CONFIRMED

`get_inbound_refs` uses a `LEFT JOIN memories sm ON sm.id = mr.source_memory_id`. When the source memory has been hard-deleted, the JOIN yields `NULL` for `source_team_id` and `source_tenant_id`. These NULLs are passed to `can_access_team_resource`, which returns `TRUE` (NULL team = universal access). The deleted memory's UUID then appears in the accessible citers list for any caller.

**Fix:** In `get_inbound_refs`, add `WHERE sm.id IS NOT NULL` (i.e. `INNER JOIN`) or filter out `sm.id IS NULL` rows before the access check. Hard-deleted sources should always be inaccessible.

---

### BUG-4 (Security) — Same NULL LEFT JOIN leak in refs_out
**File:** [src/mem_mcp/mcp/tools/refs_out.py](src/mem_mcp/mcp/tools/refs_out.py) line 59  
**Severity:** High  
**Status:** CONFIRMED

Same root cause as BUG-3 but on the outbound side. Hard-deleted target memory UUIDs surface as accessible outbound refs, potentially leaking cross-tenant deleted memory identities.

**Fix:** Same pattern — change `get_outbound_refs` to `INNER JOIN` or add a `target_tenant_id IS NOT NULL` guard before calling `can_access_team_resource`.

---

### BUG-5 (Correctness) — Re-fetch SELECT after localdev auto-create omits default_team_id
**File:** [src/mem_mcp/auth/middleware.py](src/mem_mcp/auth/middleware.py) line 245  
**Severity:** Medium  
**Status:** CONFIRMED

The initial lookup SELECT includes `ti.default_team_id`. The re-fetch SELECT executed after auto-create/backfill does not. If any future code between the re-fetch and `TenantResolution` construction reads `row['default_team_id']`, it raises a `KeyError` — only on the localdev first-login path, making it hard to catch.

**Fix:** Add `ti.default_team_id` to the re-fetch SELECT at line ~245 so both queries return the same shape.

---

### BUG-6 (Correctness) — email `or` username fallthrough writes non-email into tenants.email
**File:** [src/mem_mcp/auth/middleware.py](src/mem_mcp/auth/middleware.py) line 334  
**Severity:** Low  
**Status:** PLAUSIBLE (theoretical — Cognito does not emit `email: ""` in practice)

`claims.raw.get('email') or claims.raw.get('username')`: if `email` is present but is an empty string, Python's `or` falls through to `username` (a non-email identifier). This writes a username into `tenants.email`, breaking `ON CONFLICT (email)` deduplication on future logins with the real email.

**Fix:** Use explicit None check: `claims.raw.get('email') or None` and then separately `or claims.raw.get('username')`, or guard with `email if email else claims.raw.get('username')`.
