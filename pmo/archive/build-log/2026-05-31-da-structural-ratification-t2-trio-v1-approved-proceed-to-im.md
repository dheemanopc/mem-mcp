---
source: memsys (team: pmo)
id: 1e24baec-a54a-42f8-b7ab-23dd0c56001b
type: decision
version: 1
is_current: True
created_at: 2026-05-31T14:52:58.986184Z
updated_at: 2026-05-31T14:52:58.986184Z
tags: [approve, carry-forward-t3, current, da-structural-ratification, da-to-developer, for-developer, infrastructure, pmo, pmo-project-pmo-v1-build, pmo-task-T2, project-manifest, v1]
extracted_at: 2026-06-02
---

# DA STRUCTURAL RATIFICATION — T2 trio v1 APPROVED. Proceed to implementation (one carry-forward note).

**Written 2026-05-31 by DA (PMO domain), threaded under manifest `75e8523c`. State: `for-developer`. Verdict: APPROVE (with one carry-forward note; does not block). Structural-gate ratification of Developer submission `47c7bb06` (Reviewer-approved T2 trio v1). This is the gate AFTER design-Reviewer-approve, BEFORE implementation, per the two-gate model in DA `1e631e63`.**

Refs: Developer submission `47c7bb06` | approved trio: Plan `780ea619`, LLD `2d05c14e`, Test Plan `542b1c74` | Reviewer round-1 APPROVE `b7a2742c` | DA T2 kickoff `e47f81b2` | DA task set `4eb18941` (T2) | DA T1 verification + SF-1..SF-5 `584614ac` | PM working-memory ruling `ba6d113a` | DO gate-release `b29f1a10` | open Developer→DO escalation `e153cdb8` | infra spec `7a9007f7` D3.

## VERDICT — APPROVE

The T2 trio v1 passes structural ratification. Developer is cleared to implement per LLD `2d05c14e` + Test Plan `542b1c74`. The five structural points the submission named are all satisfied; one carry-forward note attaches (does not block, applies to T3 not T2).

## STRUCTURAL CHECKS (DA scope — cross-task / seam, distinct from the Reviewer's task-local gate)

1. **SF-5 confinement — PASSES, and tighter than the bar.** `helpers/__init__.py` is documentation-only (zero imports); `helpers/working.py` confines the only SDK symbol (`MemoryClient`) under a `TYPE_CHECKING` guard, so importing the module does not import `mem_mcp` at runtime. The pure builder `build_working_write_kwargs` is genuinely SDK-symbol-free and is the source-of-shape. Net: T2 leaks NO SDK runtime symbols into T3/T4/T5/T6/T7. Mirrors the T1 pattern that shipped verified (`584614ac`). The unit/integration split (pure-layer unit-tested SDK-free; async boundary integration-tested under `MEM_MCP_TEST_DSN`) is the same one the Reviewer required and that shipped clean in T1.

2. **No coupling to T4 (permissions) or T5/T6 (formal artifacts) — CLEAN.** LLD's explicit "NO PERMISSION CHECKS IN T2" section confirms the helpers never call `check_permission` (T6 callers gate before invoking). No formal-artifact write path; `type="note"` hardcoded, no `slug_clue` flows through builder or wrapper (working memories never get slugs — substrate fact honored). Separation of concerns matches the spine in `4eb18941`. T6 will consume these helpers unchanged.

3. **Pending-DO marker properly held — CONFIRMED.** This was DA's kickoff instruction (`e47f81b2`). `capture_user_response` is drafted in the dedicated-function direction (DO's lean per `b29f1a10`) but explicitly NOT submitted for ratification; the marker is exact across Plan, LLD, and Test Plan (skipped test files name `e153cdb8` + `e47f81b2` in the skip reason). The switch is genuinely one-line: dedicated-function stays as drafted, or convention-only deletes the function and callers pass `working_type="pmo-user-response"`. The rest of T2 (pure builder + async wrapper) is ratifiable independent of DO's answer — and IS ratified here. DA did not ratify the capture-interface shape; that remains DO's call on `e153cdb8`.

4. **`purpose`-enum deferred to v1.5 — HONORED.** No v1 code or test exercises a `purpose` parameter. PM refinement `ba6d113a` + DA kickoff `e47f81b2` both honored.

5. **R2 SDK-signature carry-forward — CORRECT DISCIPLINE.** The LLD documents the ASSUMED `MemoryClient.write(...)` signature and defers real-signature verification to impl-time, surfacing any divergence as a T2 substrate-fact addendum (extending SF-1..SF-5) rather than silently bending the design. The pure builder's output stays the source of shape; the wrapper translates if the actual Protocol differs. This is the exact discipline that produced SF-1..SF-5 in T1 and it is the right call here.

## CARRY-FORWARD NOTE (does not block T2 — lands on T3)

**DoD-2 verb substitution proves a WEAKER property than a downstream seam (T3 session-load) relies on.**

T2 spec DoD-2 (`4eb18941`) reads: "Tag-filtered `memory_list` retrieves it." The Developer substituted `memory_thread_get(manifest_root)` (Integration I1), and the Reviewer accepted it as substrate-coherent because the `MemoryClient` Protocol lacks a `list` verb. I concur it is NOT a DoD shrink for T2 — T2 is the WRITE path, and the persisted record carries the correct tags (verified by I2), so tag-filtered retrievability is a property of the stored memory, not of T2's code.

BUT the structural asymmetry must be recorded for T3: `memory_thread_get` proves retrievability BY THREAD; it does NOT exercise retrievability BY TAG-FILTER. T3 (manifest schema + session-load) explicitly relies on tag-filtered retrieval to parse the working history by tag (per the T3 DoD in `4eb18941` and the substrate fact that working memories are "reachable by thread-get + tag-list"). The tag-list retrieval path on `indexable=false` leaves is therefore UNPROVEN by the T2 test suite.

**Direction (for T3, not a T2 amendment):** when T3 is planned, its integration tests MUST include a tag-filtered retrieval assertion against `indexable=false` working leaves (i.e., prove `memory_list`/tag-filter returns them, not merely `memory_thread_get`). This closes the gap T2's substitution leaves open. T2 proceeds as-is; no change to the T2 trio. Developer: carry this into the T3 plan's DoD when T3 comes up; DA will check for it at the T3 structural gate.

## ADOPTION NOTE (non-blocking, echoing the Reviewer's observation)
The Reviewer flagged (style, non-blocking) that Unit 10's static-source fallback may be the more deterministic SF-5 primary assertion vs the `sys.modules`/`sys.path` monkeypatch (fragile if `mem_mcp` is already loaded in-session). DA concurs — adopt the static-source assertion as primary at implementation time, monkeypatch as supplementary. Same invariant, more robust. Not a gate condition.

## T2 GATE CHAIN — STATUS
DA kickoff (gate released) `e47f81b2` → Reviewer round-1 APPROVE `b7a2742c` → **DA structural ratification (this memo): APPROVE.** Next: Developer implements per LLD + Test Plan → `awaiting-verification` impl-response (references T2 `4eb18941` + infra-spec D3 `7a9007f7`; reports real `MemoryClient.write` signature per R2) → DA verification ratification → PM ratifies T2 milestone closure.

## WHAT DA WILL CHECK AT T2 VERIFICATION
- SF-5 held in shipped code (no `mem_mcp` import outside the `TYPE_CHECKING` guard; CI unit job collects clean without the SDK).
- Real `MemoryClient.write` signature reported; any divergence captured as a T2 substrate-fact addendum for T3..T7.
- Pending-DO surface resolved (or still cleanly marked if DO hasn't answered — implementation may ship the ratified core with the capture interface still pending).
- No regression on T1's verified surface.

Developer: cleared to implement. DA available for seam questions during implementation.
