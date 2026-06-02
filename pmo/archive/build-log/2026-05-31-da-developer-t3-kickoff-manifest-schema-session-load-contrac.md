---
source: memsys (team: pmo)
id: c33ae22b-9063-4893-9953-d45c5caf66bf
type: decision
version: 1
is_current: True
created_at: 2026-05-31T15:35:32.570035Z
updated_at: 2026-05-31T15:35:32.570035Z
tags: [current, da-to-developer, for-developer, infrastructure, pmo, pmo-project-pmo-v1-build, pmo-task-T3, project-manifest, sdk-substrate-facts, session-load, t3-kickoff, v1]
extracted_at: 2026-06-02
---

# DA → Developer — T3 KICKOFF: manifest schema + session-load contract (structural instructions; parallel to blocked T2)

**Written 2026-05-31 by DA (PMO domain), threaded under manifest `75e8523c`. State: `for-developer`. T2 is BLOCKED on core gap `d287353a` (status `27824fcb`); per PM/owner direction the PMO track proceeds to T3 in parallel while the owner lands the core SDK fix. This is the DA STRUCTURAL kickoff for T3. Build the T3 trio (Plan + LLD + Test Plan) against the spec; Reviewer gate then DA structural gate, same cycle as T1/T2.**

Refs: T3 spec in DA task set `4eb18941` | infra spec `7a9007f7` D2 (manifest schema) | SF-1..SF-5 `584614ac` + SF-6 lock/SF-7 defer in `6d43a262` | DA T2 carry-forward (tag-filtered retrieval) `1e24baec` | T2 blocked-status `27824fcb` | core gap `d287353a` | batch-read fix `96464537`.

## WHY T3 NOW (sequencing rationale — not a spine reorder)
T2 implementation is paused on `d287353a`. T3 is the READ side of the same working-memory system T2 writes to, and T3's PRODUCTION code does not touch the blocked write surface. So T3 builds and tests fully in parallel. This does NOT reorder the dependency spine: T6 still consumes T2; T2 resumes and lands before T6 when the core fix is live. T3 just fills the wait productively.

## T3 SCOPE (from `4eb18941` + infra spec D2)
Two halves:
1. **Manifest root schema** (per infra spec `7a9007f7` D2): the shape of the project-root memory that working history threads under (this project's root is `75e8523c`). Fields the root carries; how flat replies hang off it.
2. **Session-load contract**: the boot sequence a role runs on resume —
   - `memory_get_batch` for the fixed bundle (role def + matrix + configs + any named refs), resolving role-defs by STABLE SLUG-TUPLE (`team_id` + `decision` + `pmo-role-<role>-v1`), NOT tag-search (T3 load-contract refinement folded in `1e631e63`).
   - `memory_thread_get(manifest_root)` for the full working history, parsed BY TAG.

**Out of scope:** what the role DOES with the bundle (T6); matrix parsing/enforcement (T4).

## THE RISKY SEAM (why T3 is [S, risky])
Flat-threading + batch-read both bite here. Session-load that mishandles either silently returns partial/wrong context, and EVERY role depends on this. Specifically:
- Flat threading: working memories are one-level leaves under the root; no reply chains. `memory_thread_get(root)` must return root + ALL replies; assert leaf-count matches writes.
- Batch-read: relies on the `96464537` slug-tuple fix being live (impl-response must NAME this dependency). Partial-failure entries surface as structured per-entry errors, NOT a crash.

## SUBSTRATE FACTS (carry the SF block into the T3 plan)
- SF-1..SF-5 from `584614ac` (carry-for-chain; non-load-bearing on T3's read path).
- **SF-6 (locked, `6d43a262`):** the post-extension `MemoryClient.write` surface (`+parent_id +indexable`). Load-bearing for T3 only at the TEST-FIXTURE layer (see below), not its production read path.
- **SF-7 (deferred):** `references` on the SDK write surface is an open T5-time question; irrelevant to T3.

## CRITICAL FIXTURE GUIDANCE — DO NOT TRIP THE T2 WRITE GAP
T3's DoD requires integration tests against a seeded manifest with ≥3 working replies of mixed types, and per my T2 carry-forward `1e24baec` T3 must prove **tag-filtered retrieval of `indexable=false` leaves** (not merely thread-get retrieval). To create those leaves the way production will (threaded via `parent_id`, `indexable=false`), the fixture needs the two fields the Plugin SDK currently truncates (`d287353a`).

**Seed fixtures via the MCP TOOL LAYER directly, NOT via the plugin SDK client (`ctx.memories`).** The tool layer (`memory_write` / `memory_write_async`) already supports `parent_id` + `indexable` — that is how all working memories are written today. The fixture is test scaffolding, not plugin production code, so it is not bound by the SDK surface and is NOT blocked by `d287353a`. This keeps T3 fully buildable and testable now with ZERO rework when the SDK extension lands. Do NOT block T3, and do NOT write SDK-bypass code in T3 PRODUCTION — the bypass is fixture-only.

## DoD (from `4eb18941`, plus the carry-forward)
1. Given a manifest id, one `memory_thread_get` returns root + ALL replies; assert leaf-count matches writes.
2. Batch-load assembles the bundle (role def + matrix + configs) via `memory_get_batch`; partial-failure entries surface as structured per-entry errors, not a crash.
3. Session-load resolves role-defs by slug-tuple (never tag-search, per `1e631e63`); uses the confirmed-fixed batch-read; impl-response NAMES reliance on the `96464537` fix being live.
4. Integration-tested against a seeded manifest with ≥3 working replies of mixed types.
5. **(DA carry-forward from `1e24baec`):** tests MUST include a TAG-FILTERED retrieval assertion against `indexable=false` working leaves — prove `memory_list`/tag-filter returns them, not only `memory_thread_get`. This closes the gap T2's DoD-2 thread-get substitution left open.
6. `awaiting-verification` impl-response written, referencing T3 + infra spec D2.

## BOUNDARIES (do not absorb other owners' lanes)
- T4 (matrix parsing/enforcement) and T6 (what the role does with the bundle) are OUT. T3 gets context in the door, correctly and completely — nothing more.
- The working-memory caller-discipline (when to write) remains DO's Area A; T3 is the READ contract, unaffected.

## GATE SEQUENCE (unchanged)
Trio → Reviewer (task-local vs DoD; amend-loop ~3 rounds, escalate to DO) → on approve, submit to DA `developer-to-da` / `awaiting-da-ratification` for structural gate → implement → impl-response → DA verification ratification.

## DA POSTURE
Proceed to the T3 cycle now. DA available for seam questions during planning and holds the structural gate for your Reviewer-approved trio. When `d287353a` lands, T2 resumes in parallel; the two tracks reconverge before T6.
