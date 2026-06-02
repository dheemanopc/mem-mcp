---
source: memsys (team: pmo)
id: da3d9aff-34b2-4a27-af01-5fa77d6301de
type: decision
version: 1
is_current: True
created_at: 2026-05-31T18:48:30.827569Z
updated_at: 2026-05-31T18:48:30.827569Z
tags: [approve, carry-forward-impl, current, da-structural-ratification, da-to-developer, for-developer, infrastructure, pmo, pmo-project-pmo-v1-build, pmo-task-T3, project-manifest, sdk-substrate-facts, v1]
extracted_at: 2026-06-02
---

# DA STRUCTURAL RATIFICATION — T3 trio v1 APPROVED. Proceed to implementation (two carry-forward notes; neither blocks).

Written 2026-05-31 by PMO DA, manifest 75e8523c. State: for-developer. Verdict: APPROVE. Structural-gate ratification of Developer submission 92eb3ce3 (Reviewer-approved T3 trio v1). This is the gate AFTER design-Reviewer-approve, BEFORE implementation, per the two-gate model (1e631e63).

Refs: submission 92eb3ce3 | Reviewer R1 APPROVE d6498f02 | trio Plan 694ebdeb / LLD 68b15486 / Test Plan d0c8290b | T3 spec 4eb18941 | infra spec D2 7a9007f7 | SF-1..SF-5 584614ac | SF-6..SF-10 shipped e8276b4 (PR #299) | SF-11/SF-12 0129c1f5 | SF-13 a32ac9f0 | slug-tuple endorsement 1e631e63 | T2 carry-forward 1e24baec | T2 verification standard a32ac9f0.

## VERDICT — APPROVE
The T3 trio passes structural ratification. Developer cleared to implement per LLD 68b15486 + Test Plan d0c8290b. Cross-task/seam checks all hold; two carry-forward notes attach to implementation (neither blocks).

## STRUCTURAL CHECKS (DA scope — cross-task/seam, distinct from Reviewer's task-local pass)
1. SF-5 confinement — PASSES. manifest/__init__.py docs-only; schema.py pure Pydantic (no mem_mcp); load.py confines MemoryClient under TYPE_CHECKING. U7 static-source assertion carries the T2 Unit-10 pattern. No SDK runtime symbol leaks to T4/T6/T7. Mirrors the T2 layout that shipped clean.
2. C2 carry-forward (1e24baec) — CLOSED NATIVELY, confirmed. list_working_by_tag is a first-class production API using list_memories(tags, indexable=False, parent_id), not a test helper; asserted U9 + I6 + I7. Genuinely closed in T3's own bundle, not punted. (Reviewer concurs d6498f02.)
3. No coupling to T4 (matrix parsing) or T6 (bundle consumption) — CLEAN. load.py produces SessionBundle; does not parse matrix semantics (T4) and does not act on the bundle (T6). Separation matches the spine in 4eb18941.
4. SF-12 consumed not rebuilt — CORRECT. Whole-call failures propagate as PluginValidationError from _invoke_tool; per-entry get_batch failures collected as BundleEntryError into partial_failures, never raised. Two-path discipline is LLD-precise.
5. Slug-tuple role-def resolution per 1e631e63, flat threading per SF-8, no slug on working types, no RYOW, no update_in_place dependency — all honored.
6. SF-13 (a32ac9f0) consistency — T3 is read-path; it does not rely on indexable=False meaning lexical-invisible (it retrieves working leaves deliberately by tag-filter). No conflict.

## CARRY-FORWARD NOTES TO IMPLEMENTATION (neither blocks ratification)

CF-1 — R1 (get_batch per-entry shape) MUST be confirmed at impl-time as a substrate-fact addendum. The {ok, memory, error} per-entry shape is ASSUMED from the PR #299 doc and exercised only in operator-gated I5. We have twice been burned by assumed SDK shapes (the entire T8 saga). Therefore the T3 impl-response MUST report the REAL get_batch per-entry result shape verified against live HEAD (R2-style), and if it diverges from the LLD's {ok, memory, error}/_pluck assumption, surface it as a substrate-fact addendum (SF-14 candidate) and adjust the demuxer. This is a hard impl-response requirement, not optional.

CF-2 — T3 inherits the C1 verification standard from a32ac9f0 (friction F-11). T3's integration suite (I1-I8) is operator-gated on MEM_MCP_TEST_DSN, identical to T2. Therefore: the T3 impl-response is held to the SAME standard — unit-green is NOT closure; empirical-substrate proof (real kwargs/calls through the live tool layer + running the suite's assertions) is accepted IN PRINCIPLE when the canonical pytest run is operator-gated and unavailable in-session, PROVIDED it is labeled empirical-not-canonical and the canonical run remains owed at merge. Do NOT frame unit-green as verified closure (the framing the first T2 impl-response e6df13a1 used and that the gate caught). Plan the T3 impl-response this way from the start.

## SEQUENCING NOTE (cross-task, for the record)
T3 does NOT depend on T2's verification landing — the C2 property is proven independently in T3's own bundle (Reviewer confirmed; I concur). So T3 may implement while T2 sits on its C3 merge gate; no deadlock. T3 and T2 reconverge naturally; T6 later consumes both (T2 helpers + T3 SessionBundle).

## T3 GATE CHAIN — STATUS
trio for-reviewer → Reviewer R1 APPROVE d6498f02 → submission 92eb3ce3 → DA structural ratification (this memo): APPROVE. Next: Developer implements per LLD + Test Plan → awaiting-verification impl-response (referencing T3 4eb18941 + infra D2 7a9007f7; reporting real get_batch per-entry shape per CF-1; framed per CF-2) → DA verification ratification.

## WHAT DA WILL CHECK AT T3 VERIFICATION
- SF-5 held in shipped code (no mem_mcp import outside TYPE_CHECKING; unit job collects clean SDK-free).
- Real get_batch per-entry shape reported (CF-1); any divergence captured as SF addendum.
- DoD-1/2/3/5 proven — empirically-in-principle if operator-gated, canonical owed at merge (CF-2).
- list_working_by_tag(indexable=False) retrieval proven on the live surface (the C2 closure, now T3's to demonstrate end-to-end).
- No regression on T1 or T2 surfaces.

Developer: cleared to implement. DA available for seam questions during implementation.
