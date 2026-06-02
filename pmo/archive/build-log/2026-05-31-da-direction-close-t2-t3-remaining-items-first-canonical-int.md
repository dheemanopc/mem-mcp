---
source: memsys (team: pmo)
id: 5749a0e2-9c0f-4875-bc3b-b0dd9f653aaa
type: decision
version: 1
is_current: True
created_at: 2026-05-31T19:15:56.637340Z
updated_at: 2026-05-31T19:15:56.637340Z
tags: [current, da-direction, da-to-developer, da-to-do, for-developer, for-do, infrastructure, pmo, pmo-project-pmo-v1-build, pmo-task-T2, pmo-task-T3, pmo-task-T4, pmo-task-T5, pmo-task-T6, pmo-task-T7, project-manifest, sequencing, v1]
extracted_at: 2026-06-02
---

# DA DIRECTION — Close T2 + T3 remaining items FIRST (canonical integration + merge-confirm), THEN advance T4–T7 specs. Sequencing per owner.

Written 2026-05-31 by PMO DA, manifest 75e8523c. State: for-developer / for-do. Owner direction: Developer finishes the remaining T2/T3 closure items, then moves to the T4–T7 spec gates. Owner is driving the Developer on this. This memo makes "remaining things" concrete so closure is auditable, not loose.

Refs: T3 verification + sequencing flag 3d4fc9a9 | T2 re-verification a32ac9f0 (C1 empirical-accepted, C3) | T2 HELD e81c4332 | T3 impl-response 49bba1e5 | T3 structural ratification da3d9aff (CF-1+CF-2) | standing verification standard a32ac9f0 | T1 closure bar 584614ac | T4 6a07407d / T5 8bfb44dd / T6 35500c48 / T7 c9095015.

## CONTEXT UPDATE (owner-confirmed)
T2 (PR #1) and T3 (PR #2) are MERGED + DEPLOYED to prod. This clears the merge portion of T2's C3 and removes the earlier sequencing flag (A) about unconfirmed merge claims — the merges are real per owner. What remains is the canonical verification that the standing standard (a32ac9f0) said was OWED AT MERGE. Merge has happened; therefore that run is now DUE.

## STEP 1 — CLOSE T2 (remaining items only)
- C2: already closed (a32ac9f0).
- C1: accepted empirical-in-principle; the canonical run was owed at merge.
- REMAINING: run the canonical integration suite (T2 I1–I7) against the LIVE deployed instance; re-confirm prod plugin-discovery shows the pmo helpers live (parity with T1 bar 584614ac); refile a short T2 verification-confirmation (merge commit c42f3b0 + canonical results + prod re-confirm) for-da.
- On that: DA issues T2 VERIFIED. PM then ratifies the T2 milestone.
- IF operator/DSN wiring still does not exist post-deploy: say so explicitly; DA will close T2 on the accepted-empirical proof with the canonical-run gap RECORDED as a known lower-assurance closure (honest, labeled) rather than blocking. Do NOT claim canonical-VERIFIED without the run.

## STEP 2 — CLOSE T3 (remaining items only)
- CF-1 (real get_batch shape): already satisfied (49bba1e5) — matched LLD, no addendum.
- CF-2 framing: correct.
- REMAINING: run the canonical integration suite (T3 I1–I8) against live (DoD-3 via semantic-style query per SF-13 where applicable); re-confirm prod; refile T3 verification-confirmation (merge commit bb986a9 + canonical results) for-da.
- On that: DA issues T3 VERIFIED.
- Same operator/DSN fallback as Step 1 if wiring absent: accepted-empirical close, gap recorded, labeled honestly.

## STEP 3 — THEN advance T4 → T5 → T7 → T6 (NOT before Steps 1-2)
Per owner, the four trios were drafted together (legitimate — owner-directed; the earlier "front-running" flag is withdrawn). They now enter gates IN DEPENDENCY ORDER, after T2/T3 are closed:
- T4 (matrix loader + permission-check) — independent of T5/T6/T7; gates first.
- T5 (reference-spine writer) — independent; gates next.
- T7 (registration + self-discovery) — composes on T2 (now closed); gates.
- T6 (generic role-tool engine) — convergence point; consumes T2+T3+T4+T5; gates LAST among engine pieces, after its inputs are at least structurally ratified.
Each: trio already for-reviewer → Reviewer task-local pass → DA structural ratification → impl → DA verification. Reviewer may begin task-local passes in parallel; DA structural ratification proceeds in the order above.

## RESERVED FOR DA (unchanged, restated so it is not lost)
The T6 trio (35500c48) carries the SF-11 update-mechanic STRUCTURAL CALL (write-fresh vs update-in-place for working memories, per 0129c1f5). That is an explicit DA ruling at the T6 structural gate — NOT settled by the trio's own framing and NOT swept through a Reviewer batch. DA rules on it separately when T6 reaches the gate.

## NET
Developer: Steps 1-2 first (close T2/T3 with canonical runs, or accepted-empirical + recorded gap if DSN absent), then Step 3 (T4→T5→T7→T6 gates in order). DO: keep T2/T3 IN-FLIGHT until DA VERIFIED issues per Steps 1-2; do not pre-close. DA stands ready to issue both VERIFIEDs on the refiled confirmations, and to run the gates in dependency order.
