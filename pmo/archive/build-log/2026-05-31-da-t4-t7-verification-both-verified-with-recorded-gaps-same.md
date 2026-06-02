---
source: memsys (team: pmo)
id: e225a631-5966-4503-97cc-30b94f7b71cb
type: decision
version: 1
is_current: True
created_at: 2026-05-31T19:59:17.494490Z
updated_at: 2026-05-31T19:59:17.494490Z
tags: [current, da-to-developer, da-to-do, da-verification, for-developer, for-do, infrastructure, pmo, pmo-project-pmo-v1-build, pmo-task-T2, pmo-task-T4, pmo-task-T7, project-manifest, t2-reconfirm, t4-verified-with-gaps, t7-verified-with-gaps, v1]
extracted_at: 2026-06-02
---

# DA T4 + T7 VERIFICATION — both VERIFIED-WITH-RECORDED-GAPS (same level as T2/T3). CF-T4-1 + CF-T7-1 satisfied (T7 took the preferred path). One added obligation: T2 re-confirm after the extra_tags change. T5/T6 direction already issued.

Written 2026-05-31 by PMO DA, manifest 75e8523c. State: for-developer / for-do. Verifies combined T4+T7 impl-response a426e101 against c372360c carry-forwards + the standing CF-2 standard a32ac9f0. Verdict: T4 and T7 both close VERIFIED-WITH-RECORDED-GAPS, same lower-assurance level as T2/T3 (operator-runbook ceiling unchanged). Adds one obligation (T2 re-confirm) created by the extra_tags extension. The Ask-#2 (T5/T6 path) is ALREADY answered in d9b1891e — pointing there, not re-ruling.

Refs: T4+T7 impl-response a426e101 | batched ratify + CFs c372360c | T5 blocker + my direction d9b1891e | standing standard a32ac9f0 | T2/T3 closure pattern d038512c | T2 closure d038512c | specs 4eb18941 (T4) / 7dcbb2c8 (T7).

## T4 — VERIFIED-WITH-RECORDED-GAPS
ACCEPTED:
- CF-T4-1 satisfied: PluginContext shape reported from HEAD (contract.py:298-307; tenant_id NIL at startup). Loader takes matrix_team_id EXPLICITLY, not ctx.tenant_id. Exactly as required.
- SF-5 confined (U-T4-14); role-count-agnostic (U-T4-10, 4+6-role) green; pure parse/check/invariant green; on_startup wiring with fail-fast on invalid/drift + graceful-warn if DO content absent.
RECORDED GAP (T4-G1): DoD-1 (allowed-pass / disallowed-raise) is proven IN CODE but its tests (U-T4-11/12) are at the mem_mcp-gated integration tier, not pure-unit — so the core permission behavior's green is SDK-gated. Same empirical-pending posture as the rest; runs at merge with SDK installed, or empirical-in-principle.
RESIDUAL OBLIGATION T4-O1 (owed): execute the check_permission allowed/disallowed integration tests (mem_mcp-gated, no DSN needed) at merge and report; this one does NOT need the operator DSN, only the SDK installed — so it is dischargeable sooner than the DSN-gated ones.

## T7 — VERIFIED-WITH-RECORDED-GAPS
ACCEPTED:
- CF-T7-1 resolved via the PREFERRED alternative: write_working extended with extra_tags; registration routes through it. Single enforcement point preserved (indexable=False / parent_id / type discipline not re-implemented inline). This is the stronger outcome — the T2-helper-as-sole-guarantee invariant is intact. Good.
- SF-11 option (a) correctly bound: register writes FRESH; latest_registration_for recency-resolves by created_at; working carries no slug. Ruling NAMED in code + impl-response.
- Registration tag schema NAMED for DO (DoD-4): pmo / pmo-working / pmo-role-<role> / pmo-project-<slug> / pmo-registration + pmo-identity-<uuid> + optional pmo-state-<state>; cross-project discovery excludes pmo-project. DO encodes this in vocabulary.
- SF-5 confined (U-T7-5); pure tag-builder + query-construction tests green.
RECORDED GAP (T7-G1): DoD-1/2/3 leaf-roundtrip + re-registration-recency + self-discovery-exclusion are operator-DSN-gated (I-T7-*), not executed. Same level as T2/T3/T4. Empirical-in-principle acceptable if DSN stays absent.

## ADDED OBLIGATION FROM THIS PR — T2 RE-CONFIRM (important; do not skip)
PR #3 MODIFIED T2's shipped code: helpers/working.py write_working + build_working_write_kwargs now accept optional extra_tags. T2 is a CLOSED task (d038512c). Touching a closed task's surface requires a light re-confirm that the additive change did not regress T2's own guarantees:
- T2-RC1: extra_tags is OPTIONAL with a default that leaves existing T2 behavior byte-identical (no extra_tags → exact prior tag set + indexable=False forced). Confirm via the existing T2 unit tests still asserting the no-extra_tags path unchanged.
- The impl-response reports 54 unit tests pass INCLUDING T2's 10 — so this is very likely already satisfied. I am RECORDING it, not blocking: T2's closure stands, but the T4+T7 verification-confirmation (or merge note) must explicitly state that T2's no-extra_tags behavior is unchanged + indexable=False still forced. This keeps the audit trail honest that a closed task was extended.
(This is a backward-compatible additive change and the right design — CF-T7-1 preferred path. The obligation is just to not silently mutate a closed task's verified surface.)

## NET CLOSURE
- T4: CLOSED — VERIFIED-WITH-RECORDED-GAPS. Gaps T4-G1; obligations T4-O1 (SDK-gated check_permission tests, dischargeable at merge without DSN).
- T7: CLOSED — VERIFIED-WITH-RECORDED-GAPS. Gap T7-G1 (DSN-gated).
- T2: closure STANDS; add re-confirm T2-RC1 (state no-extra_tags regression) at the T4/T7 merge note.
- Both at the same assurance level as T2/T3; PM ratifies as lower-assurance closures.
DO: ledger — T4 + T7 CLOSED-WITH-GAP; carry T4-O1, T7-G1, T2-RC1.

## T5 / T6 — ALREADY DIRECTED (d9b1891e), not re-ruled here
The impl-response's Ask-#2 (confirm SDK extension + owner-vs-core authoring) is ALREADY ANSWERED in d9b1891e (written before the Developer saw it): slug_clue gap CONFIRMED, filed as core feature request #2 (f924de70 in the memsys-core team, +team_id bundled), ReferenceInput (b) confirmed as mechanical impl-adjust, T5+T6 HOLD, owner-authorization for the core PR is the same separate decision as PR #299. Developer: read d9b1891e for the full T5/T6 direction. No new ruling needed.

## STANDING — operator-runbook ceiling (now 5 tasks)
T1-deferred, T2, T3, T4, T7 have all closed at lower assurance for the same reason. The PM/owner recommendation in d038512c (stand up operator runbook + test DSN ONCE) is now even stronger — 5 tasks would retro-upgrade to canonical, and T4-O1 (SDK-gated, no DSN) plus the eventual T5/T6 could close canonically. Still PM/owner's call.

Developer: T4+T7 closed-with-gaps; add the T2-RC1 statement at merge; T5/T6 per d9b1891e; T4-O1 dischargeable without DSN. DA ready for T5 once the SDK gap lands, and for the T2/T3/T4/T7 canonical upgrade if the runbook stands up.
