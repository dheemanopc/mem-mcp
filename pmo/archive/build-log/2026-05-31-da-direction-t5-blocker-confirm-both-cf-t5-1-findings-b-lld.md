---
source: memsys (team: pmo)
id: d9b1891e-eb36-4715-8670-1aa4c66e07ba
type: decision
version: 1
is_current: True
created_at: 2026-05-31T19:39:27.882981Z
updated_at: 2026-05-31T19:39:27.882981Z
tags: [current, da-direction, da-to-developer, da-to-do, for-developer, for-do, infrastructure, memsys-core, pmo, pmo-project-pmo-v1-build, pmo-task-T5, pmo-task-T6, project-manifest, sdk-substrate-gap, t5-impl-blocker, v1]
extracted_at: 2026-06-02
---

# DA DIRECTION — T5 blocker: CONFIRM both CF-T5-1 findings. (b) LLD-adjust, no gap. (a) slug_clue SDK gap CONFIRMED → file core feature request (+team_id), same pattern as 3d1145c7. T5+T6 HOLD; T4+T7 proceed.

Written 2026-05-31 by PMO DA, manifest 75e8523c. State: for-developer / for-do. Rules on T5 impl blocker 812441f8 (CF-T5-1 verification per c372360c). CF-T5-1 did its job — both assumed shapes were wrong; caught at impl-start, not post-merge. Verdict: (b) confirmed as mechanical LLD adjustment; (a) confirmed as a real SDK gap, filed as a memsys-core feature request sibling to 3d1145c7; T5+T6 hold, T4+T7 proceed in parallel.

Refs: T5 blocker 812441f8 | DA batched ratify + CF-T5-1 c372360c | T8 write-gap precedent (filing pattern) 3d1145c7 + DA direction 6d43a262 | repo-placement discipline 50e11ec8 | T5 trio 8bfb44dd | core SDK gap (new) — filed in memsys-core team this turn (see below).

## CF-T5-1(b) — ReferenceInput shape: CONFIRMED, mechanical, no gap
The live shape (write.py:48-68) is reference_kind / target_uuid / refs_version (NOT kind / to), with extra="forbid" — the LLD's {kind,to} guess would have hard-failed. The references kwarg IS on the SDK Protocol (contract.py:127), so this is a pure CALL-SITE correction, no SDK gap, no trio re-ratification. T5 adopts at impl:
references=[{"target_uuid": parent_id, "reference_kind": "derived-from", "refs_version": "pinned"}]
APPROVED as a mechanical impl-time adjustment. The public write_formal_artifact signature is unchanged.

## CF-T5-1(a) — slug_clue SDK gap: CONFIRMED hard blocker
Diagnosis confirmed. Substrate (write.py:87) has slug_clue, REQUIRES it for decision/fact, REJECTS metadata-smuggling. SDK Protocol (contract.py:118-128) truncates it. Identical class to the original write-gap 3d1145c7 (parent_id+indexable) that PR #299 fixed. T5 DoD-4 ("formal artifact receives a slug") is UNREACHABLE through ctx.memories.write as the SDK stands. NOT a T5 design error — a second SDK-surface truncation.

RULING (mirrors the T8 precedent exactly):
- This is a MEMSYS-CORE SDK gap, not a PMO change. Filed as a core feature request sibling to 3d1145c7 (filed into the memsys-core team this turn — see the core-gap memo). PMO does NOT author the core PR opportunistically (repo-placement 50e11ec8 / 6d43a262); core-PR authoring is a SEPARATE owner authorization, exactly as for PR #299.
- SCOPE: add slug_clue: str | None = None AND team_id: UUID | None = None to the Protocol + MemoryClientImpl.write, forwarding both into MemoryWriteInput. I am INCLUDING team_id (the Developer flagged it as similarly truncated) — bundling avoids a THIRD round-trip when a later task needs cross-team formal writes. One core extension, two fields, same shape as PR #299's two-field fix. Defaults preserve all existing callers (kite/reminders use type=note, incompatible with slug_clue anyway).
- New substrate fact on landing: SF-14 (proposed) — MemoryClient.write exposes slug_clue + team_id, forwarded to MemoryWriteInput; slug_clue REQUIRED for decision/fact, REJECTED for note/snippet/question. I'll lock SF-14 when the extension deploys (same as SF-6 lifecycle).

## SEQUENCING — CONFIRMED
- T4 implementation — PROCEED (no slug_clue dependency; matrix loader + check + on_startup). Already started; good.
- T7 implementation — PROCEED (registration writes are working-type, not formal; no slug_clue). Already started; good.
- T5 — HOLD on the slug_clue SDK extension landing on prod.
- T6 — HOLD ENTIRE (not partial). Endorsed: T6's generality-proof DoD needs formal-put (→T5), and partial-T6 would force a split verification + rework when T5 lands. Cleaner to hold T6 intact until T5 unblocks, per the "in one go" intent. T6 also still carries the SF-11 ruling already locked (option a) in c372360c.

## RESUME CONDITION (T5 → then T6)
slug_clue+team_id SDK extension merged+deployed on memsys-core prod → Developer re-verifies the live write signature (CF-1 discipline; SF-14 lock) → T5 implements (with the (b) ReferenceInput correction) → T5 impl-response → DA verification → then T6 implements against T2+T3(live)+T4+T5 → T6 impl-response (NAMING the SF-11 option-a ruling) → DA verification.

## NOTE — this is the 2nd SDK truncation found by the report-real-shapes discipline
CF-1/CF-T5-1 caught this at impl-START, before any wasted build. That is the discipline paying off (it would otherwise have surfaced as a runtime rejection mid-T5-build or post-merge). Worth a friction-log entry: the SDK Protocol was extended reactively for parent_id/indexable (PR #299) but slug_clue/team_id were left truncated — suggesting the SDK-vs-substrate parity should be audited ONCE comprehensively rather than gap-by-gap. (Recommend to memsys-core architect, not a PMO mandate.)

Developer: file/await the core extension; (b) adopt at impl; T4+T7 continue; T5+T6 hold. DO: track the new core gap alongside the queue; T3-O1 still owed. DA filed the core feature request into the memsys-core team this turn.
