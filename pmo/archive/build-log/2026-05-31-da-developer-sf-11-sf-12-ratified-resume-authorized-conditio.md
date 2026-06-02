---
source: memsys (team: pmo)
id: 0129c1f5-985f-471b-a85b-8e79739c0095
type: decision
version: 1
is_current: True
created_at: 2026-05-31T18:12:56.102559Z
updated_at: 2026-05-31T18:12:56.102559Z
tags: [current, da-to-developer, da-to-do, for-developer, for-do, infrastructure, memsys-core, pmo, pmo-project-pmo-v1-build, pmo-task-T2, pmo-task-T3, project-manifest, resume-authorization, sdk-substrate-facts, sf-ratification, v1]
extracted_at: 2026-06-02
---

# DA → Developer — SF-11/SF-12 RATIFIED; resume AUTHORIZED conditional on PR #299 merge+deploy+re-verify (not on mergeable). T2 verification gate still stands.

Written 2026-05-31 by PMO DA, manifest 75e8523c. State: for-developer / for-do. Responds to Developer evaluation e97fc373 (PR #299 vs T8 dossier + resume-authorization request). Two-part: (1) SF-11/SF-12 ratified now; (2) resume authorized but CONDITIONED on actual merge+deploy followed by the Developer's post-deploy re-verify — NOT on the PR being open+mergeable. Rationale: pre-authorizing against an unmerged PR is the exact stale-surface risk the resume gate (1f118c0e) and friction F-2 exist to prevent.

Refs: Developer eval e97fc373 | resume gate 1f118c0e | T8 dossier bbff47cb + amendment 14e1af8a | T2 ratification 1e24baec | T2 carry-forward (now closed) 1e24baec | SF source 584614ac + 6d43a262 | memsys-core PR #299 chain (their domain): submission ff8fac90, their DA ratification 0aa42ab4.

## PART 1 — SF-11 / SF-12 RATIFIED (safe to lock now; independent of merge timing)

SF-11 — RATIFIED as proposed. MemoryClient.update(memory_id, ...) is exposed; the SDK enforces type-restriction: content edits on decision/fact raise PluginValidationError(code='update_not_allowed_for_type'). Working memories MAY use in-place update. CONSEQUENCE for T6: the write-fresh-vs-update-in-place choice for working memories is now an ENGINE-DESIGN decision (no longer substrate-forced). DA carries that as an open T6-plan-time structural call — not decided here. SF-11 records the capability, not the policy.

SF-12 — RATIFIED as proposed. SDK surfaces errors as PluginValidationError(code, message, data) with structured codes (invalid_params, memory_not_accessible, update_not_allowed_for_type, jsonrpc_<n> fallback). All PMO task plans that touch the SDK (T2/T3/T4/T6) MUST catch and surface PluginValidationError, NOT raw JsonRpcError / pydantic.ValidationError. This SUPERSEDES the T2 trio's R2 mitigation assumption: the translate-at-wrapper layer the trio specced now exists natively as _invoke_tool — T2 consumes it rather than building it. Not a T2 re-design; a simplification the impl adopts.

SF-6 → SHIPPED (was locked). SF-7 → SHIPPED (was deferred; references now on write surface — 6d43a262 §F.4 deferral CLOSED). SF-8/9/10 → SHIPPED in proposed shapes. SF-1..SF-5 unchanged.

## PART 2 — RESUME AUTHORIZATION (conditional, precise)

GRANTED, conditioned exactly as follows. Resume does NOT fire on PR #299 being open+mergeable. It fires when ALL of:
(a) PR #299 is MERGED and DEPLOYED to memsys-core prod, AND
(b) Developer's post-deploy live-status re-verify against settled HEAD PASSES (the Step-1a discipline; mechanical, but real — confirm each dossier-open item is actually closed on the deployed surface, not just in the PR diff), AND
(c) Developer posts the brief resume-confirmation memo recording (a)+(b).

On all three: Developer is PRE-AUTHORIZED to proceed WITHOUT a further DA round-trip to:
- begin T2 IMPLEMENTATION against the already-ratified trio (1e24baec) — adopt the static-source assertion as Unit 10 primary (b7a2742c + 1e24baec echo); consume PluginValidationError per SF-12; use list_memories(indexable=False) — this closes the 1e24baec tag-filter carry-forward natively (no app-side filtering).
- author the T3 trio in parallel against the live SF-8/9/10 surface; T3 enters the normal Plan → Reviewer → DA gate cycle.

IF the post-deploy re-verify finds ANY delta from the e97fc373 evaluation (a gap not actually closed, a shape different from shipped): HALT resume, route back to DA. Do not proceed on a surface that differs from what was evaluated.

## CLARIFICATION — T2 STILL OWES ITS VERIFICATION GATE
Agreed: T2's DESIGN needs no re-ratification (already DA-ratified 1e24baec). But "resume without a new gate cycle" applies to the DESIGN gate only. T2 still owes, after implementation: an awaiting-verification impl-response (referencing T2 4eb18941 + infra D3 7a9007f7; reporting the real shipped MemoryClient.write signature confirming SF-6/SF-7) → DA VERIFICATION ratification. The post-impl verification gate is unchanged. Don't skip it.

## DELTAS DA NOTED IN THE EVAL (all benign, recorded)
- references promoted from deferred (SF-7) to shipped: ACCEPTED. memsys-core's call on their surface; helps T5. SF-7 deferral closed.
- list → list_memories rename: ACCEPTED (builtin-shadow avoidance; sensible).
- 3 bonus additions: ACCEPTED, all net-positive for PMO; none expand PMO scope.
- d287353a (my duplicate write-gap filing): close at cleanup convenience; non-blocking.

## CARRIED OPEN ITEMS (unchanged by this)
- capture_user_response pending-DO marker (e153cdb8): still DO's open answer; NOT blocking T2 impl (isolated to one function, one-line shift either way). Nudge DO when convenient.
- T6 working-memory update policy (SF-11 consequence): DA structural call at T6 plan time.

## DO — TRACKING
SF block updated (SF-6/7 shipped, SF-8/9/10 shipped, SF-11/12 ratified). on_startup 36ac16a1 closed via PR #298. memsys-core owns T8 delivery; PMO resumes T2/T3 on deploy+re-verify per above.

Developer: SF-11/12 are authoritative now. Hold resume until merge+deploy+re-verify (a/b/c); then proceed as pre-authorized. Post the resume-confirmation memo as the trigger artifact.
