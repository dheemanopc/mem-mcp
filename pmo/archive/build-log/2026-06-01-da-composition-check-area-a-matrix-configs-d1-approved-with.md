---
source: memsys (team: pmo)
id: 3de9c504-2fdf-40b4-af09-6c972aa32976
type: decision
version: 1
is_current: True
created_at: 2026-06-01T05:39:03.415751Z
updated_at: 2026-06-01T05:39:03.415751Z
tags: [current, d1-composition-check, da-composition-check, da-to-developer, da-to-do, for-developer, for-do, infrastructure, matrix-configs-check, pmo, pmo-project-pmo-v1-build, pmo-task-T6, project-manifest, resolver-contract, t6-go-live-gate, v1]
extracted_at: 2026-06-02
---

# DA COMPOSITION-CHECK — Area A + matrix/configs + D1: APPROVED with ONE required correction. The six default_list_query resolvers are NOT all the escalation tag-filter (D1 ask-b overclaims). Escalation mechanism itself composes cleanly. Cheap fix; D1 stays Option B.

Written 2026-06-01 by PMO DA, manifest 75e8523c. State: for-do / for-developer. Composition-check of the completed DO content track per F4 (7dcbb2c8) — the two confirmations DO named as the T6-go-live gate (7c9553c3). Verdict: Area A (940cfbae) APPROVED as-is; matrix+configs (238b450b) APPROVED with no matrix change needed; D1 sealed-fallback (ced035fd) escalation MECHANISM approved, but its composition ASK (b) overclaims and needs ONE correction before the six resolvers are implemented. Net: T6-go-live is GATED on that correction + the six resolver implementations.

Refs: matrix+configs 238b450b | D1 note ced035fd | Area A 940cfbae | DO track-complete note 7c9553c3 | capture answer bb55f05b | schema 7dcbb2c8 | PM bar + D1 lock 2b256cad | SF-11 ruling c372360c | T6 verification f5d94bff.

## (1) AREA A (940cfbae) — APPROVED AS-IS
Thin vocabulary is internally consistent and consistent with shipped mechanisms: role codes canonical (never key off long-form — matches T7), §7 standard tag set matches T2's enforced 5-tag + indexable=false (SF-13), §6 registration schema matches shipped T7, §5 working-memory discipline matches the PM ruling + durability caveat, §3 reference-kinds match T5/SF-15. No defect. Ratified as the vocabulary surface.

## (2) MATRIX + CONFIGS (238b450b) — APPROVED; invariants independently re-verified
I re-ran both load-time invariants against the actual JSON (not on DO's PASS claim):
- INVARIANT 1 (config verbs ⊆ matrix cell, per (role,resource)): HOLDS for all six roles. Spot-confirmed the tricky ones — review_verdict per-role class split (formal for Reviewer create/read/list; working read/list for DA+Developer) is schema-supported and matrix-consistent. delete granted to no role (audit-trail intact) — consistent.
- INVARIANT 2 (every default_list_query ∈ resolver registry): all six names present.
- roles[] == matrix keys == configs keys: aligned.
The matrix itself needs NO change. It correctly flips T6 from inert (pmo_matrix_not_loaded) to live once parsed.

## (3) D1 SEALED-FALLBACK (ced035fd) — MECHANISM APPROVED; ASK (b) OVERCLAIMS — ONE CORRECTION REQUIRED

ASK (a) — CONFIRMED. The pmo-escalation-to-<role> routing tag IS the single canonical surfacing key, and it cleanly resolves the original three-way pending_intake / pending_ratifications / assigned_intake mismatch into one convention. The escalation surfacing mechanism COMPOSES correctly on existing verified substrate: write via T2 write_working (indexable=false, parent_id=manifest); surfacing via T7-style tag-filtered list_memories(indexable=false) with SF-10 strict-AND; responds-to edge via T5 references (SF-15 refs_out traversal); open/resolved via write-fresh state leaf (SF-11 discipline, recency-resolved). All four assertions T-D1-1..4 are real and testable. The escalation infra is sound — exposed-by-default with a recoverable seal, meeting the 2b256cad "real, not paper" bar.

ASK (b) — REJECTED AS STATED; requires correction. The note claims the per-role default_list_query intake names in 238b450b "should EACH resolve to the tag-filter in §2 — i.e. the six named resolvers T6 must implement INCLUDE this escalation query as their intake surface." This conflates two different surfaces. The six resolvers are NOT all the escalation query:
- pm_pending_intake, dm_pending_intake — intake-flavored; escalations-to-role ARE a primary input. Escalation tag-filter is CORRECT here (possibly unioned with pending ratifications).
- da_pending_structural_ratifications — the DA's primary queue is TRIOS/PROPOSALS AWAITING THE DA STRUCTURAL GATE (developer-to-da / awaiting-da-ratification), NOT escalations-to-DA. If this resolver returned the escalation tag-filter, the DA's default list would surface escalations and MISS its actual ratification queue — a functional regression. Escalations-to-DA are a SECONDARY surface.
- pa_pending_structural — proposals/milestones awaiting PA structural; not escalations.
- developer_assigned_tasks — tasks assigned to Developer (state-filtered registration/story query); not escalations.
- reviewer_pending_reviews — proposals/trios awaiting a Reviewer verdict (the review queue); not escalations.

So the escalation tag-filter is ONE INPUT to the intake-flavored resolvers, NOT the definition of all six. D1's loop-closing claim must be narrowed to: "the escalation tag-filter (pmo-escalation, pmo-escalation-to-<role>, pmo-escalation-open) is A component query that each role's intake/pending surface MAY union in; it is the canonical ESCALATION surface, not the canonical definition of every default_list_query." With that narrowing, D1 composes and seals.

## REQUIRED CORRECTION (cheap — D1 stays Option B, does NOT flip to Option A)
DO: revise ced035fd ask (b) to the narrowed claim above. The escalation MECHANISM is unchanged (approved); only the overstated "all six resolvers = escalation query" coupling claim is corrected. This is the cheap fix 2b256cad's fallback clause anticipates — composition holds, so D1 does NOT flip to lock-first Option A.

## CONSEQUENT — THE SIX RESOLVER SPECS (DA structural input for the Developer's implementation)
Since the six resolvers are distinct, each needs a defined surface before/with implementation. DA structural definition (Developer implements; this is the resolver contract):
- pm_pending_intake → UNION of (escalations: pmo-escalation-to-pm + open) and (ratifications awaiting PM: pmo-role-pm + ratification-type + open-state). 
- pa_pending_structural → proposals/milestones tagged for PA structural review, open-state.
- dm_pending_intake → UNION of (escalations-to-dm + open) and (epics/stories awaiting DM intake).
- da_pending_structural_ratifications → PRIMARY: trios/proposals in awaiting-da-ratification (developer-to-da). Escalations-to-da unioned as secondary.
- developer_assigned_tasks → tasks/stories with assignment to developer identity, state-filtered (claimed/in-progress), via registration tag schema (Area A §6).
- reviewer_pending_reviews → proposals/trios in for-reviewer / awaiting-review state.
Each resolver returns loud pmo_named_query_unknown only if the NAME is unregistered (CF-T6-1); a registered resolver with no matches returns empty-list, not error. Developer: implement these six against the SF-10 tag-filter + state tags; escalation-to-<role> is the shared sub-query the intake ones union in.

## NET — T6-GO-LIVE GATE STATUS
- Area A: ratified.
- Matrix+configs: ratified (no change).
- D1: mechanism ratified; ask-(b) correction required (cheap; DO revises the coupling claim only).
- Six resolvers: DA contract defined above; Developer implements; that is the LAST code before T6 is live-operable.
On the D1 correction + the six resolver implementations landing (+ a seeded matrix on prod), T6 goes live and DoD-4 (T6-G1, live permission-denial) becomes provable end-to-end. This is the path to closing the last live gap.

DO: narrow D1 ask-(b) per above. Developer: implement the six resolvers per the DA contract. DA: will confirm the resolver implementations at their gate, and re-confirm D1 once narrowed. This + a prod matrix seed = T6 live = v1 functionally complete (modulo the operator-runbook canonical-assurance upgrade, still PM/owner's call).
