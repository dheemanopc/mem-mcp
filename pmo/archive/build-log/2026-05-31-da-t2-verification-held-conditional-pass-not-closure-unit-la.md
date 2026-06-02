---
source: memsys (team: pmo)
id: e81c4332-c6db-4a2f-9060-9f73c94e49aa
type: decision
version: 1
is_current: True
created_at: 2026-05-31T18:31:14.931001Z
updated_at: 2026-05-31T18:31:14.931001Z
tags: [current, da-to-developer, da-to-do, da-verification, for-developer, for-do, infrastructure, pmo, pmo-project-pmo-v1-build, pmo-task-T2, project-manifest, t2-verification-held, v1]
extracted_at: 2026-06-02
---

# DA T2 VERIFICATION — HELD (conditional pass, NOT closure). Unit layer + SDK-shape reporting verified; DoD-1/2/3 integration round-trip UNEXECUTED + PR unmerged. Three conditions to close.

Written 2026-05-31 by PMO DA, manifest 75e8523c. State: for-developer / for-do. Verification-gate ruling on T2 impl-response e6df13a1. Verdict: HELD — T2 is NOT verified-closed yet. The design, unit layer, and real-SDK-shape reporting pass; but the integration round-trip that proves T2's core DoD is unexecuted, and PR #1 is unmerged. Closing T2 now would be a false closure (the exact over-claim the gate exists to prevent, per DO closure principle fb94b12d).

Refs: T2 impl-response e6df13a1 | T2 design ratification 1e24baec | T2 spec 4eb18941 | trio Plan 780ea619 / LLD 2d05c14e / Test Plan 542b1c74 | carry-forward (tag-filter retrieval) 1e24baec | resume auth + T2-verification-still-owed clarification 0129c1f5 | T1 verification precedent (merge+live required) 584614ac | SF-12 0129c1f5.

## WHAT VERIFIES (5 of 6 checks pass)
1. Real SDK shapes reported + match SF-6/SF-7 — PASS. write() signature reported verbatim from contract.py:118-128 post-merge; parent_id+indexable present (SF-6), references present (SF-7), keyword-only correct defaults. Helpers pass parent_id=manifest_root + indexable=False.
2. SF-12 _invoke_tool adoption — PASS. T2 consumes the native wrapper; PluginValidationError propagates unwrapped to callers. Correct surface; clean simplification, no redesign.
3. Unit 10 static-source primary — PASS, as approved (b7a2742c + 1e24baec). More deterministic than monkeypatch; same SF-5 invariant.
4. Pending-DO marker preserved — PASS, across helper + both sentinel test files; one-line switch retained.
5. No T1 regression — PASS. 22 passed + 2 skipped; T1's 12 tests intact.

## WHY HELD (the blocking gap)
6. PR implements trio without scope creep — CANNOT CONFIRM AS CLOSURE, because the DoD core is unproven:

DoD-1 (write lands as leaf under root, exact tags, indexable=False), DoD-2 (tag-filtered retrieval), DoD-3 (semantic search does NOT return it) are the PROPERTIES T2 EXISTS TO GUARANTEE — and per the impl-response's own DoD↔evidence table, their integration tests (I1-I6) are marked "Operator-run" / "deferred to operator" = NOT EXECUTED. Only the unit layer (pure builder dict-shape, in isolation) is green.

What is verified: the builder constructs the right kwargs. What is NOT verified: that those kwargs, through the LIVE SDK, produce a correctly-threaded, non-indexable, tag-retrievable, search-invisible memory. The unit tests structurally cannot prove that; only the live integration round-trip can, and it has not run. This is the whole point of T2.

Compounding (both self-reported in e6df13a1):
- PR #1 is OPEN, unmerged, unreviewed (plugin repo needs a reviewer). Unmerged code is not shipped. T1's closure precedent (584614ac) required merge + live prod confirmation; T2 holds to the same bar.
- DoD-2's integration test uses thread_get for retrieval only. With list_memories(indexable=False) now LIVE (PR #299), the carry-forward in 1e24baec REQUIRES the integration test to assert TAG-FILTERED retrieval via list_memories — not only thread_get — because that is the property T3 session-load depends on. The impl-response does not show list_memories used in I1.

## THREE CONDITIONS TO CLOSE T2 (clear all → DA ratifies VERIFIED)
C1. EXECUTE the integration suite I1-I6 against live memsys (operator/integration run with MEM_MCP_TEST_DSN). Report results. DoD-1/2/3 must pass empirically: leaf-under-root + indexable=False asserted in storage; semantic search returns empty for the UUID-sentinel.
C2. AUGMENT DoD-2's integration assertion to retrieve the working leaf via list_memories(tags=..., indexable=False) — proving TAG-FILTER retrieval, not only thread_get. This closes the 1e24baec carry-forward at T2 rather than punting it to T3. (Small addition; the live surface now supports it.)
C3. PR #1 REVIEWED + MERGED, and the plugin re-confirmed loading on prod (parity with T1's closure bar: plugin_discovery shows pmo helpers live, CI green on main).

On C1+C2+C3 reported: DA issues T2 VERIFIED ratification; THEN PM ratifies the T2 milestone. Not before.

## WHAT THIS IS NOT
Not a design reopen (design stays ratified 1e24baec). Not a criticism of the impl — the Developer reported the deferred-integration status accurately in the DoD table; the gap is that "impl-response filed + unit-green" was framed as closure when the integration gate and merge are still open. This is the verification gate doing its job: catch the difference between "code written" and "DoD proven on the live surface."

## INTERACTION WITH T3
The Developer deferred T3 authoring this session (per 44bc4047, owner directed a closure response + new task). Fine. Note for whoever resumes: T3 should NOT be ratified assuming T2's tag-filter retrievability is proven until C2 is met — if T2 closes C2, T3 inherits a proven property; if T3 proceeds first, it must carry the assertion itself (the original 1e24baec carry-forward).

## CARRIED (unchanged)
- capture_user_response pending-DO (e153cdb8): DO's open answer; not blocking; one-line switch.
- T6 working-memory update policy (SF-11): DA structural call at T6 plan time.

Developer (next session): clear C1+C2+C3, refile the impl-response with integration results + merge confirmation, route for-da. DA closes on that. DO: T2 stays IN-FLIGHT in your closure ledger — do not mark closed until DA VERIFIED issues.
