---
source: memsys (team: pmo)
id: b29f1a10-047d-4f8a-8abc-3cd264a1dc59
type: decision
version: 1
is_current: True
created_at: 2026-05-31T14:06:43.843944Z
updated_at: 2026-05-31T14:06:43.843944Z
tags: [pmo, do-to-da, do-to-developer, for-da, for-developer, working-memory-discipline, t2-gate-release, pmo-project-pmo-v1-build, v1, current]
extracted_at: 2026-06-02
---

# DO ACK of PM working-memory refinement → releases DA's T2 gate; folds discipline into Area A

**Written 2026-05-31 by DO (PMO domain), threaded under manifest `75e8523c`. State: `for-da` (gate-release) + `for-developer` (Area A input). Acknowledges PM refinement `ba6d113a`, which resolves the working-memory question the DA routed via `eec72021` and DO escalated via `c38a096b`. The PM independently reached reading (a). This memo closes the loop: confirms T2 is unblocked and commits the Area A consequences.**

Refs: PM refinement `ba6d113a` (the ruling) | DA escalation `eec72021` (held the T2 gate) | DO→PO memo `c38a096b` (DO's converging recommendation) | T2 spec in `4eb18941` | infra spec `7a9007f7` D3.

## RESOLUTION: reading (a). T2 UNCHANGED. DA GATE RELEASES.

The PM ruling `ba6d113a` and DO's recommendation `c38a096b` converged independently on the same answer: working memories are CROSS-BOUNDARY BRIDGES, not session notebooks. Session retention covers within-role self-continuity; memsys covers what crosses roles/sessions/audit. The owner's flag targeted self-continuity notes (redundant now), NOT cross-role persistence (non-negotiable).

**Consequence the DA was waiting for:** T2's MECHANISM is unchanged (auto `indexable=false`, tag set, manifest threading, `pmo-user-response` capture all stand). Only CALLER DISCIPLINE changes, which is prompt/convention, not T2 code. **The held T2 structural gate (`eec72021`) can RELEASE.** The Developer's T2 plan proceeds against the spec as written; nothing in the DoD shrinks.

## WHAT DO OWNS OUT OF THIS (Area A folding)

The PM named DO's action as "role-config records with no change" — correct, configs already tag/thread properly. But the PM ruling has a real home in DO's Area A (the working-memory convention DO authors), and DO commits to fold in:

1. **The WHEN / WHEN-NOT criteria** from `ba6d113a` become the working-memory-discipline section of Area A, verbatim-aligned. The PM designated its memo "the authoritative complement" to infra spec `7a9007f7` D3; Area A will reference both as the single convention surface.

2. **DO's one value-add on top of the PM ruling — the DURABILITY caveat.** Session retention is convenience, NOT a durable store. A session lost (crash, context overflow, deliberate reset) before its work is bridged to memsys loses retained-but-unwritten state. So Area A's rule is: **"retain for self, persist for others — AND persist anything a post-loss resume will need."** This closes the one gap the "don't write self-notes" discipline could otherwise reopen (the data-loss failure mode working-memory persistence originally guarded). It does NOT contradict the PM ruling — it's the boundary condition on "self-continuity is the session's job": true while the session lives; memsys is the fallback when it doesn't. DO will state it as a narrow exception, not a re-expansion of self-note writing.

3. **The `purpose`-parameter idea (PM's, for T2 v1.5)** — DO notes it as a future hardening that would make the discipline mechanical (enum `cross_role_handoff|user_response|audit_decision|stabilized_handoff`, refuse self-continuity writes). DO agrees it's v1.5, not v1; prompt discipline suffices for the demo. Flagged so it's tracked, not built now.

## DO POSTURE / NEXT

DO applies the discipline to itself immediately (this memo is a cross-role bridge: gate-release for DA + Area A input for Developer — it qualifies). DO's next authored artifact remains **Area A (the thin vocabulary + working-memory convention)**, now incorporating the PM discipline + durability caveat. The Developer's separate T2 intent question (`e153cdb8`: user-response capture as dedicated function vs tag) is still open and DO-owned; DO answers it next, independently of this resolution (lean: dedicated function — the verbatim contract is a framework concern, and it aligns with the PM's "user-response capture REQUIRED" standing).

DA: gate released, proceed to the T2 cycle when the Developer submits. DO carries Area A.
