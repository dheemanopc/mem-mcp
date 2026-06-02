---
source: memsys (team: pmo)
id: d038512c-8896-4e4f-a298-254bc8afa09f
type: decision
version: 1
is_current: True
created_at: 2026-05-31T19:22:09.899441Z
updated_at: 2026-05-31T19:22:09.899441Z
tags: [current, da-to-developer, da-to-do, da-to-pm, da-verification, for-developer, for-do, for-pm, infrastructure, pmo, pmo-project-pmo-v1-build, pmo-task-T2, pmo-task-T3, project-manifest, t2-verified-with-gaps, t3-verified-with-gaps, v1]
extracted_at: 2026-06-02
---

# DA T2 + T3 VERIFICATION — both VERIFIED-WITH-RECORDED-GAPS (lower-assurance closure per pre-authorized option-2). Two specific residual obligations named. NOT unqualified VERIFIED.

Written 2026-05-31 by PMO DA, manifest 75e8523c. State: for-developer / for-do / for-pm. Rules on Developer combined verification-confirmation 322019ee against DA direction 5749a0e2 (which pre-authorized lower-assurance closure if operator/DSN wiring stayed absent — it did). Verdict: T2 and T3 both close as VERIFIED-WITH-RECORDED-GAPS. This is honest closure at the assurance level the evidence supports — NOT unqualified canonical VERIFIED, and NOT a block. Two specific residual obligations are named and owed.

Refs: confirmation 322019ee | DA direction 5749a0e2 (option-2 authorization) | standing standard a32ac9f0 | T2 empirical sentinel 21e55047 | T2 HELD e81c4332 | T3 impl-response 49bba1e5 | T3 structural ratification da3d9aff | T1 closure bar 584614ac.

## ASSURANCE FRAMING (why "VERIFIED-WITH-RECORDED-GAPS", not plain VERIFIED)
Canonical pytest integration could not run (operator/DSN fixture wiring never materialized post-deploy — the persistent blocker). Per 5749a0e2 I pre-authorized closing on accepted-empirical proof + merge with the canonical gap RECORDED. I am exercising that. But closure honesty requires naming the SPECIFIC residuals, not a generic "canonical owed". The Developer was commendably honest about where evidence is weaker (flagged E-T3-C as lower-confidence rather than overclaiming) — that honesty is exactly why I can close this without re-litigating.

## T2 — VERIFIED-WITH-RECORDED-GAPS
ACCEPTED:
- C2 closed (list_memories(indexable=False) assertion I7; already accepted a32ac9f0).
- C1 empirical: the 21e55047 sentinel proof of DoD-1/2/3 working-write substrate behavior via live tools stands (already accepted).
- C3 merge: PR #1 merged c42f3b0, CI green at merge.
RECORDED GAP (T2-G1): prod plugin-discovery re-confirm is NECESSARY-BUT-NOT-SUFFICIENT as provided. readyz=200 proves memsys-core lifespan succeeded but, by the Developer's own honest accounting, does NOT prove the PMO plugin's tools actually registered (per-plugin try/except per PR #298 records a plugin failure without killing lifespan). The T1 bar (584614ac) was stricter — it confirmed pmo_ping actually registered. T2's live-registration-on-prod is therefore NOT directly confirmed.
RESIDUAL OBLIGATION T2-O1 (owed): confirm via tools/list on the MCP endpoint (or equivalent) that the T2 helper surface / pmo plugin tools are live-registered on prod — the strict T1-parity check. Discharge when MCP transport access or operator runbook exists.

## T3 — VERIFIED-WITH-RECORDED-GAPS
ACCEPTED:
- CF-1 satisfied (real get_batch shape matched LLD; 49bba1e5).
- CF-2 framing honored (unit-green never claimed as closure).
- E-T3-A (SF-10 strict-AND tag-filter + indexable=False) — genuinely demonstrated live this session. Solid.
- E-T3-B (thread_get returns root + ALL replies; manifest get) — genuinely demonstrated live (the 442KB+ thread_get this session structurally proves root+all-replies at scale). Solid.
RECORDED GAP (T3-G1): E-T3-C (get_batch partial-failure demux path) was NOT empirically demonstrated — get_batch was not callable from the session; the Developer falls back to a CODE-SHAPE inference (the SDK path invokes the same verified-shape substrate tool). Reasonable inference, but an inference, not a demonstration. And this is the RISKIEST part of T3 — it is exactly R1 from the structural gate (da3d9aff), the partial-failure {ok,memory,error} demux. Unit tests U-T3-11..15 prove the demuxer LOGIC; what is unproven end-to-end is the live get_batch RETURNING a partial-failure entry in the asserted shape.
RESIDUAL OBLIGATION T3-O1 (owed): empirically demonstrate (or canonically test) load_session_bundle's partial-failure path — a get_batch call with one known-bad entry returning {ok:false, error:{code,message}} and landing as BundleEntryError on bundle.partial_failures. Discharge when get_batch is callable in-session OR operator runbook/DSN exists.

## NET CLOSURE STATE
- T2: CLOSED — VERIFIED-WITH-RECORDED-GAPS. Gap T2-G1; obligation T2-O1 owed.
- T3: CLOSED — VERIFIED-WITH-RECORDED-GAPS. Gap T3-G1; obligation T3-O1 owed.
- Both closures are AUDIT-HONEST: the residuals are named specifically, not hidden under "done". A future audit sees exactly what was proven (core paths, empirically, live) vs inferred (T2 live-registration; T3 partial-failure demux).
- PM may now ratify the T2 + T3 milestones AS lower-assurance closures (PM should see this gap framing, not a clean "verified").
- DO: mark T2 + T3 CLOSED-WITH-KNOWN-GAP in the closure ledger; carry T2-O1 + T3-O1 as open obligations to discharge when operator wiring exists.

## STANDING NOTE — operator-runbook wiring is now the recurring assurance ceiling
Three tasks now (T1 deferred, T2, T3) have hit the same wall: integration suites authored but never canonically run because the operator/DSN fixture wiring does not exist. This is no longer a per-task footnote; it is a systemic assurance ceiling on the whole plugin track. RECOMMEND (PM/owner decision, not DA's to mandate): before T4–T7 close, stand up the operator runbook + a MEM_MCP_TEST_DSN test instance once, so the back half of the spine can close at canonical (not lower) assurance. Logged as a friction item.

## STEP 3 UNBLOCKED
Per 5749a0e2, T2+T3 closure unblocks the T4→T5→T7→T6 structural gates in dependency order. The Developer reports trios Reviewer-APPROVED (f67d548e); they now come to DA for structural ratification per task, in order. T6 carries the SF-11 STRUCTURAL CALL — DA rules separately at T6's gate (pre-lean recorded: option (a) write-fresh for working / supersede for formal, but the explicit ruling happens at the gate).

Developer: T2+T3 closed with the two named obligations; proceed to submit T4 for structural ratification (then T5, T7, T6 in order). DA ready.
