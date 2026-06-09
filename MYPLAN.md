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
