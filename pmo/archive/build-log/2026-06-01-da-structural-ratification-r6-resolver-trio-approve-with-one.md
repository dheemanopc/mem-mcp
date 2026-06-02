---
source: memsys (team: pmo)
id: ea3627ab-f743-44d5-9d70-9cb01e40bc59
type: decision
version: 1
is_current: True
created_at: 2026-06-01T06:26:52.244074Z
updated_at: 2026-06-01T06:26:52.244074Z
tags: [approve-with-fix, current, da-structural-ratification, da-to-developer, for-developer, infrastructure, pmo, pmo-project-pmo-v1-build, pmo-task-T6, project-manifest, resolver-ratification, t6-go-live, v1]
extracted_at: 2026-06-02
---

# DA STRUCTURAL RATIFICATION — R6 resolver trio: APPROVE WITH ONE REQUIRED FIX (indexable-tier defect on DA + DM resolvers) + verdicts on R-R-1/2/3. Cheap fix, no redesign.

Written 2026-06-01 by PMO DA, manifest 75e8523c. State: for-developer. Structural-gate ratification of R6 trio e2b261a1 (Reviewer-approved 9ca3dd71, submitted f95c41d7) against DA spec 42022af0 + composition-check 3de9c504. Verdict: APPROVE, conditioned on ONE required implementation fix — an indexable-tier correction the Reviewer could not catch in-bundle (it requires cross-project knowledge of how submissions are written). The escalation mechanism, union semantics, module layout, and test structure are all correct.

Refs: trio e2b261a1 | Reviewer APPROVE 9ca3dd71 | submission f95c41d7 | DA spec 42022af0 | composition-check 3de9c504 | matrix+configs 238b450b | Area A 940cfbae | D1 ced035fd | T5 formal tag pattern | this session's submission convention (every developer-to-da memo is type=decision/indexable=True).

## VERDICT — APPROVE WITH ONE REQUIRED FIX

### THE REQUIRED FIX (RF-1) — indexable tier is wrong on two resolvers
This is a cross-task structural defect, invisible to the Reviewer's task-local bundle (it requires knowing how submissions are written across the whole project — which is the DA gate's job, not the Reviewer's).

(a) da_pending_structural_ratifications — DEFECT. The resolver issues:
    list_memories(tags=["pmo","developer-to-da","awaiting-da-ratification"], indexable=False, ...)
But EVERY developer→DA structural submission in this project — including THIS trio's own submission f95c41d7 — is written type=decision, indexable=TRUE (they are formal-class routing artifacts, not working leaves). As written, this resolver queries indexable=False and returns NOTHING — it misses every actual ratification submission. The DA's default-list would be silently empty. 
REQUIRED: change the PRIMARY query to indexable=True. (The escalation SECONDARY correctly stays indexable=False — escalations ARE working memories. So the DA resolver legitimately queries BOTH tiers: indexable=True primary + indexable=False escalation secondary. That's correct once split.)

(b) dm_pending_intake — DEFECT (same class). The "awaiting DM" primary queries pmo-state-ready at indexable=False, but epics/stories are formal work-items (indexable=True, they get slugs via T5). REQUIRED: the work-item primary → indexable=True; the escalation portion stays indexable=False.

General principle for RF-1: a resolver's indexable flag must match the CLASS of what it queries — formal artifacts (submissions, ratifications, proposals, work-items, review_verdicts) are indexable=True; working memories (escalations, registrations, user_responses) are indexable=False. A resolver that unions both tiers issues TWO queries with the correct flag each (the DA + DM resolvers do union both; they just had the primary flag wrong). Audit every resolver's primary against this at impl: pa_pending_structural (primary indexable=True ✓ already correct), reviewer_pending_reviews (for-reviewer primary indexable=True ✓ correct), developer_assigned_tasks (registrations indexable=False ✓ correct). Only DA + DM primaries need the flip.

### R-R-1 (developer-to-da / awaiting-da-ratification tags not in Area A) — VERDICT: ACCEPT as-is, with the DO-v1.5 enumeration the Developer proposed.
These ARE the routing convention this whole project ran on (legitimate cross-role hand-off content per Area A §5). Not invented. The impl-response names them as convention-in-practice and recommends DO add them to Area A §7 explicitly in v1.5. Acceptable — they are real and consistently used; formalizing in Area A is housekeeping, not a blocker.

### R-R-2 (pmo-formal-ratification tag assumed for the ratification resource) — VERDICT: VERIFY AT IMPL, hard.
The pm_pending_intake ratification query and the dm "ratification" surfaces assume a tag (pmo-formal-ratification) the Developer inferred from the T5 pmo-formal-<type> pattern. This is exactly the assumed-shape risk that bit us repeatedly (F-12). REQUIRED: at impl, confirm the ACTUAL ratification resource tag against how T5/T6 write formal ratifications (and against matrix+configs 238b450b resource-type naming). If the real tag differs, use the real one. Report it in the impl-response. Do not ship the assumed tag unverified.

### R-R-3 (ordering: primary-then-secondary, not recency-merged) — VERDICT: ACCEPT.
Developer's call per the spec's explicit latitude. Primary-first preserves caller choice; recency-merge is cheap caller-side if wanted. Name it in the impl-response (already planned). Fine.

## STRUCTURAL CHECKS THAT PASS
- escalation_for: matches D1 ced035fd §1 tag shape verbatim; implemented once; reused 5-way as a COMPONENT (not the definition of all six) — honors the 3de9c504 narrowed claim. Correct.
- developer_assigned_tasks: correctly cross-project (excludes pmo-project-<slug>, Area A §6); registration surface indexable=False correct; identity-required loud-raise correct.
- pmo_resolver_missing_context: right SF-12/CF-T6-1 loud discipline (U-R-9/12).
- Module layout: edits engine/registry.py only, SF-5 TYPE_CHECKING-guarded already; no new SDK surface; composes on shipped SF-10. No memsys-core dependency.
- Unknown-name loudness preserved with six registered (U-R-2 regression on CF-T6-1).
- Test structure: U-R-1..12 SDK-independent with StubMemoryClient recording call args is the right pattern; I-R-1..7 DSN-gated per the standing CF-2 ceiling.

## NET
APPROVE for implementation, conditioned on RF-1 (DA + DM primary queries → indexable=True; audit all six) + R-R-2 (verify the real ratification tag at impl). RF-1 is a flag correction on two queries, NOT a redesign — the trio's architecture is sound. No Reviewer re-spawn needed (RF-1 doesn't change scope; it corrects an implementation detail the Reviewer's bundle couldn't surface). 

Implementation note: add a unit test asserting the DA + DM PRIMARY queries issue indexable=True (so RF-1 can't silently regress) — i.e., U-R-4 and U-R-6 should assert the primary's indexable flag explicitly, not just the tags.

Developer: implement with RF-1 + R-R-2 verified; frame impl-response per CF-2 (unit-green not closure; integration owed at the test-env/matrix-seed). On the impl-response, DA runs verification. This + matrix seeded on prod = T6 live.
