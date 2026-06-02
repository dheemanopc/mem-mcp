---
source: memsys (team: pmo)
id: f5d94bff-e576-4290-9eea-7ff3d2498d0d
type: decision
version: 1
is_current: True
created_at: 2026-06-01T04:08:58.171907Z
updated_at: 2026-06-01T04:08:58.171907Z
tags: [current, da-to-developer, da-to-do, da-to-pm, da-verification, for-developer, for-do, for-pm, infrastructure, pmo, pmo-project-pmo-v1-build, pmo-task-T5, pmo-task-T6, project-manifest, sf-14, sf-15, sf-16, spine-complete, t5-verified-with-gaps, t6-verified-with-gaps, v1]
extracted_at: 2026-06-02
---

# DA T5 + T6 VERIFICATION — both VERIFIED-WITH-RECORDED-GAPS. SF-11 option (a) bound + TESTED (both branches). SF-14/15/16 RATIFIED. Implementation spine COMPLETE. + two honest non-code gaps to the finish line.

Written 2026-06-01 by PMO DA, manifest 75e8523c. State: for-developer / for-do / for-pm. Verifies combined T5+T6 impl-response 2b4f7900 against c372360c (SF-11 option a) + d9b1891e (T5/T6 resume) + CF-2 standard a32ac9f0. Verdict: T5 and T6 both close VERIFIED-WITH-RECORDED-GAPS, same level as T2/T3/T4/T7. SF-14/15/16 ratified. This closes the T1–T7 IMPLEMENTATION spine. Two non-code gaps remain to a live demo (stated below) — "spine complete" is NOT "demo-ready".

Refs: T5+T6 impl-response 2b4f7900 | resume confirmation aec41aaf | batched ratify + SF-11 ruling c372360c | T5/T6 direction d9b1891e | core PR #300 13d4327 (SDK Tier-2 parity) | closure pattern d038512c/e225a631 | standing standard a32ac9f0.

## T5 — VERIFIED-WITH-RECORDED-GAPS
ACCEPTED:
- CF-T5-1(a): slug_clue now on Protocol (PR #300, contract.py:128); T5 passes it through. Gap closed at source.
- CF-T5-1(b): real ReferenceInput shape adopted {target_uuid, reference_kind, refs_version}; asserted U-T5-A + engine→T5 dispatch U-T6-D.
- SF-5 confined (U-T5-H static-source). No regression (new formal/ package, zero edits to existing modules).
RECORDED GAP (T5-G1): DoD-1 (refs_out/refs_in traversal), DoD-2 (bad-parent rejects whole write), DoD-3 (hard-delete blocked while inbound ref) are DSN/operator-gated (I-T5-1/2/3), not executed. DoD-4 slug passthrough pure-green; slug_lookup integration deferred. Empirical-in-principle acceptable if DSN absent — same as prior tasks.

## T6 — VERIFIED-WITH-RECORDED-GAPS
ACCEPTED:
- SF-11 OPTION (a) BOUND AND TESTED — this is the reserved structural call (c372360c), and it is not merely claimed: working put/update → T2 write-fresh; formal put → T5; formal update → memories.supersede. U-T6-E asserts working-update is write-fresh NOT supersede; U-T6-F asserts formal-update IS supersede. Both branches tested. Correct and verified.
- CF-T6-1: resolve_named_query("unknown") raises PluginValidationError(code=pmo_named_query_unknown, data={name, known}) (U-T6-B). Loud, not silent-empty. Satisfied.
- CF-T6-2: delete verb stub raises explicit NotImplementedError (U-T6-J). Satisfied.
- DoD-5 generality proof GREEN (U-T6-L): developer + reviewer configs, zero class extension — the engine's whole purpose, proven.
- SF-5 confined (U-T6-K). No regression.
RECORDED GAP (T6-G1): DoD-4 (permission denial raises T4 structured error) is unit-proven with a stub-mocked check_permission; live integration with a seeded matrix is deferred (DSN + DO matrix content). See "non-code gaps" below — this is the part that needs DO's matrix to prove end-to-end.

## SUBSTRATE FACTS — SF-14 / SF-15 / SF-16 RATIFIED
- SF-14 — RATIFIED. MemoryClient.write exposes slug_clue, expires_at, ttl_seconds (HEAD 13d4327). slug_clue REQUIRED for decision/fact, REJECTED for note/snippet/question (substrate validator). team_id NOT added to write (stays on list_memories) — acceptable; no cross-team formal write in v1 scope. expires_at/ttl_seconds bonus, unused v1.
- SF-15 — RATIFIED. MemoryClient.refs_in(memory_id)/refs_out(memory_id) expose substrate reference traversal; T5 DoD-1 consumes them.
- SF-16 — RATIFIED non-load-bearing v1. MemoryClient.slug_lookup exposed; carried, unused by T5/T6 v1.
SF block is now SF-1..16, all current as of 13d4327.

## IMPLEMENTATION SPINE — COMPLETE
T1✓ T2✓ T3✓ T4✓ T5✓ T6✓ T7✓ — all seven tasks have passed DA verification (T1 clean; T2 clean post-discharge; T3/T4/T5/T6/T7 VERIFIED-WITH-RECORDED-GAPS). The PMO plugin's CODE is implemented, unit-green, and (T2/T3/T4/T7) merged; T5/T6 in PR #4 mergeable. The engine (T6) dispatches working→T2 / formal→T5, permission-gated→T4, loads context→T3, registers→T7. Architecturally the build is done.

## TWO NON-CODE GAPS TO A LIVE DEMO (honest finish-line accounting — NOT my gate to close, but I will not let "spine complete" imply "done")
1. OPERATOR-RUNBOOK CEILING — now 7 tasks at lower assurance for one missing reason: no MEM_MCP_TEST_DSN + fixture wiring, so no canonical integration run anywhere. Standing up a pgvector test instance + wiring the stubbed fixtures ONCE would retro-upgrade all 7 to canonical. PM/owner decision (escalated since d038512c; reinforced). NOTE per the pgvector question this session: the container satisfies the DSN condition but does NOT by itself wire the stubbed fixtures, and does NOT confirm the SDK→substrate path runs without full app context — Developer must verify those two before assuming green.
2. DO CONTENT TRACK — the engine (T6) is verified but dispatches against a PERMISSION MATRIX + 6 ROLE-CONFIGS that DO still owes (schema 7dcbb2c8), plus Area A and the D1 seam note long-outstanding to DA. T6-G1 (live permission-denial) cannot be proven end-to-end until DO's matrix is seeded. So the engine is CODE-COMPLETE but not LIVE-OPERABLE until DO's content lands. This is now the genuine critical path — code is no longer the blocker; DO content is.

## NET
- T5, T6: CLOSED — VERIFIED-WITH-RECORDED-GAPS. Gaps T5-G1, T6-G1.
- SF-14/15/16 ratified; SF block 1..16 current.
- Spine implementation COMPLETE; PM may ratify the milestone set as lower-assurance closures.
- Remaining to live demo: (1) operator runbook/pgvector for canonical assurance, (2) DO matrix+configs for live operability. Both non-code; both owner/DO.
DO: ledger — T5+T6 closed-with-gap; the critical path is now DO's matrix/configs + Area A + the owed D1 seam note. Developer: PR #4 ready to merge; T4-O1 discharges post-deploy; carry T3-O1/T5-G1/T6-G1 for the canonical run if the runbook stands up.

Developer + DO: implementation done. The build now waits on DO content and the runbook decision, not on code.
