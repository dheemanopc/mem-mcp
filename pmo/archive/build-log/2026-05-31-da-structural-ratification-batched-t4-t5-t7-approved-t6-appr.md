---
source: memsys (team: pmo)
id: c372360c-0da6-450f-beed-7d3429864ee0
type: decision
version: 1
is_current: True
created_at: 2026-05-31T19:30:27.521695Z
updated_at: 2026-05-31T19:30:27.521695Z
tags: [approve, batched-ratification, current, da-structural-ratification, da-to-developer, da-to-do, for-developer, for-do, infrastructure, pmo, pmo-project-pmo-v1-build, pmo-task-T4, pmo-task-T5, pmo-task-T6, pmo-task-T7, project-manifest, structural-call-sf-11, v1]
extracted_at: 2026-06-02
---

# DA STRUCTURAL RATIFICATION (BATCHED) — T4, T5, T7 APPROVED; T6 APPROVED + SF-11 STRUCTURAL CALL RULED (option a). Per-task impl carry-forwards. + T2-O1 discharged by cross-domain deploy confirmation.

Written 2026-05-31 by PMO DA, manifest 75e8523c. State: for-developer. Rules on batched submission b31827ab / re-request 2ec213ab (Reviewer-approved f67d548e). Verdict: all four trios STRUCTURALLY RATIFIED. Processed in dependency order (T4, T5, T7 independent; T6 convergence last). T6's SF-11 update-mechanic ruled explicitly below. Each carries impl-time conditions. Batched in time; each task independently ratified.

Refs: submission b31827ab | re-request 2ec213ab | Reviewer APPROVE f67d548e | T4 6a07407d | T5 8bfb44dd | T6 35500c48 | T7 c9095015 | T4-T6 spec 4eb18941 | T7 spec 7dcbb2c8 | SF-1..13 (584614ac/6d43a262/0129c1f5/a32ac9f0/PR299) | engine decision d0604273 | slug endorsement 1e631e63 | cross-domain deploy confirmation 3566e1fd.

## PRELIMINARY — T2-O1 DISCHARGED (good news, fold into T2 closure)
The memsys-core Developer's cross-domain deploy confirmation (3566e1fd) provides exactly the strict T1-parity evidence T2-G1/T2-O1 was missing: prod restart shows plugin_discovery_complete: count=3, ids=[reminders, kite, pmo], AND a Python import smoke confirming mem_mcp_skill_pmo.helpers.working + .manifest.load are importable on prod (commit bb986a9). T2-O1 (live tool-registration confirm) is now DISCHARGED — T2's recorded gap T2-G1 closes. T2 upgrades from VERIFIED-WITH-RECORDED-GAPS to VERIFIED on the registration dimension; only the canonical pytest run remains as a (now-moot, since prod-proven) lower-assurance note. DO: update the ledger — T2-O1 discharged by 3566e1fd. (T3-O1 get_batch partial-failure demo still owed.)

## T4 — RATIFIED (matrix loader + permission-check)
Pure parse/check split from SDK boundary clean; load-time invariant (config verbs ⊆ matrix cells) with fail-fast; structured pmo_operation_not_permitted never silent. SF-5 confined (U-T4-14). Gates first; depends only on its own surface + on_startup.
CF-T4-1 (impl-time): on_startup ctx shape is freshly shipped (PR #298) and R-T4-2 notes ctx.tenant_id=NIL_UUID at startup. The design correctly passes matrix_team_id EXPLICITLY rather than ambient ctx — confirm the real on_startup ctx shape against live HEAD at impl (CF-1 discipline) and report; the explicit-team-id read must be exercised, not assumed.

## T5 — RATIFIED (reference-spine writer)
Single-layer wrapper; closes deferred SF-7 correctly (references native for parent-link; supersede reserved for content-evolution). SF-5 confined (U-T5-3). Refuse-on-failure delegated to substrate, not swallowed. Independent; gates after T4.
CF-T5-1 (impl-time, HARD): TWO assumed SDK shapes MUST be CF-1 real-shape-verified against write.py HEAD before merge — (a) slug_clue transport (the LLD guesses metadata={"slug_clue":...} but flags it may be top-level) and (b) the references element shape ({kind, to} vs the actual ReferenceInput required fields/enum). We have been burned twice by assumed shapes; these are exactly such assumptions. Report both real shapes in the impl-response; adjust call sites if divergent (public function shape stays).

## T7 — RATIFIED (registration + self-discovery)
Honors the e4e61a71 explicit-tool-call lean (registration is per-session with runtime payload; on_startup is per-app NIL-tenant — correct reasoning). Cross-project self-discovery by design (F1). SF-5 confined (U-T7-5). Independent of T4/T5; composes on T2 (now prod-live). Gates after T5.
CF-T7-1 (impl-time): R-T7-1 — T7 bypasses T2's write_working and calls memories.write directly to inject identity/state tags, re-implementing the indexable=False discipline inline rather than routing through T2's enforcing helper. ACCEPTABLE (mirrors T2's call site), BUT the impl MUST include an explicit assertion that the direct-write path always sets indexable=False + parent_id (a unit test on the call site, not just discipline). Preferred alternative if cheap: extend write_working with extra_tags and route T7 through it, restoring the single enforcement point. Developer's choice; either closes the erosion. Flagging so the T2-helper-as-sole-guarantee invariant isn't quietly weakened.

## T6 — RATIFIED + SF-11 STRUCTURAL CALL: OPTION (a)
Engine design ratified: config-driven RoleToolEngine, permission-gated via T4, working→T2 / formal→T5, generality proven by two configs zero new code (I-T6-1/2). SF-5 confined (U-T6-12). Convergence point — consumes T2+T3+T4+T5; correctly ratified LAST. SF-12 propagation correct across all delegated boundaries.

SF-11 UPDATE-MECHANIC — DA RULES OPTION (a): write-fresh for working memories; supersede for formal artifacts. This is my explicit structural ruling, not the trio's framing accepted by default. Reasoning locked:
- Preserves the chronological-thread-under-manifest invariant that T2 (leaf writes), T3 (thread_get load), and T7 (recency-resolution of re-registration) ALL depend on. In-place update would mutate the row and break recency-resolution + lineage-by-thread.
- memory_update does not preserve history; the thread does. supersede preserves history for formal types where lineage-by-UUID matters.
- Option (c) (parametrize update_mode per resource via config) would require a 7dcbb2c8 schema delta routed to DO — UNWARRANTED for v1.
- The memory_update / correct_in_place capability (SF-11) is real and useful but DEFERRED to v1.5 as an explicit separate verb, NOT folded into update. T6 v1 update = write-fresh (working) / supersede (formal).
RULING BINDS: T6 DoD-2 stands as written (working updates write-fresh). Impl-response NAMES this ruling (DoD-6).
CF-T6-1 (impl-time): R-T6-1 — NAMED_RESOLVERS v1 set is initial; DO's real configs may name resolvers not in it. The loud pmo_named_query_unknown (not silent-empty) is the correct mitigation — confirm it raises with data.known listing available resolvers. CF-T6-2: delete verb is v1-stubbed (NotImplementedError, explicit message) per 4eb18941 — acceptable; test asserts the stub.

## CROSS-CUTTING (all four)
- CF-2 framing carried by every trio (unit-green ≠ closure; canonical-or-empirical owed at merge) — good, and given the operator-runbook ceiling, expect these to close at the same VERIFIED-WITH-RECORDED-GAPS level as T2/T3 unless the runbook gets stood up (the PM/owner recommendation in d038512c).
- CF-1 real-shape discipline applies to every impl-response (T4 startup-ctx, T5 references+slug_clue, T6 supersede surface, T7 write call). Report real shapes; addendum on divergence.

## GATE CHAIN STATUS
All four: trio → Reviewer R1 APPROVE f67d548e → submission b31827ab → DA structural ratification (this memo): APPROVE ×4 + SF-11 ruled. Next per task: implement per LLD+Test Plan → awaiting-verification impl-response (CF-1 real shapes + per-task CF conditions + CF-2 framing + T6 names the SF-11 ruling) → DA verification ratification.

## IMPLEMENTATION ORDER (dependency, not strict-serial)
T4 + T5 + T7 may implement in parallel (mutually independent). T6 implements after T4/T5/T7 land (it consumes their surfaces). Each closes through its own verification gate.

Developer: all four structurally ratified; SF-11 ruled option (a) — bind it in T6. Implement per the CF conditions. DO: T2-O1 discharged (3566e1fd); update ledger; T3-O1 still owed. DA ready for the impl-responses.
