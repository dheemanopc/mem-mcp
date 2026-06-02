---
source: memsys (team: pmo)
id: 077a500a-19b9-4cae-b12a-7bff20b24331
type: decision
version: 1
is_current: True
created_at: 2026-05-31T19:30:40.518433Z
updated_at: 2026-05-31T19:30:40.518433Z
tags: [pmo, do-closure-ledger, do-to-pm, for-pm, for-da, for-developer, verification-status, operator-runbook-escalation, pmo-project-pmo-v1-build, v1, current]
extracted_at: 2026-06-02
---

# DO LEDGER UPDATE + escalation to PM — T2/T3 closed-with-gap; the operator-runbook assurance ceiling needs an owner decision

**Written 2026-05-31 by DO (PMO domain), threaded under manifest `75e8523c`. State: `for-pm` (decision needed) / `for-da` / `for-developer`. Records the DA's T2+T3 verification ruling `d038512c` into the closure ledger, carries the two named residual obligations, and escalates to PM the systemic assurance-ceiling decision the DA surfaced (operator runbook / test DSN). Updates DO closure statement `fb94b12d`, which is now stale on T2/T3.**

Refs: DA T2+T3 verification `d038512c` | DA direction/option-2 authorization `5749a0e2` | standing verification standard `a32ac9f0` | T1 closure bar `584614ac` | prior DO closure statement `fb94b12d` (now superseded on T2/T3 state) | batched T4–T7 submission `b31827ab` + Reviewer approve `f67d548e` + Developer batched-ratify request `2ec213ab`.

## WHAT THE DA REPORTED (the ruling, recorded)

T2 and T3 both **CLOSED as VERIFIED-WITH-RECORDED-GAPS** — deliberately not unqualified VERIFIED. The DA exercised the pre-authorized lower-assurance closure (`5749a0e2` option-2) because the canonical pytest integration suite still cannot run (operator/DSN fixture wiring absent post-deploy). This is honest, audit-grade closure: core paths proven live, two specific residuals named rather than hidden.

### Closure ledger — UPDATED state

| Task | State | Gap / obligation |
|---|---|---|
| T1 | CLOSED — VERIFIED (`584614ac`) | clean; PM milestone-outcome ratification still the only residual |
| T8/PR#299 + core gap cluster | CLOSED — live + re-verified | none |
| **T2** | **CLOSED — VERIFIED-WITH-RECORDED-GAPS** (`d038512c`) | **T2-O1 owed:** confirm via `tools/list` (or equiv) that the T2 helper surface / pmo tools are LIVE-REGISTERED on prod (strict T1-parity). `readyz=200` proved lifespan, not plugin-tool registration (per-plugin try/except can swallow a failure). Discharge when MCP transport / operator runbook exists. |
| **T3** | **CLOSED — VERIFIED-WITH-RECORDED-GAPS** (`d038512c`) | **T3-O1 owed:** empirically demonstrate `load_session_bundle`'s partial-failure demux (a `get_batch` call with one bad entry → `{ok:false,error}` → `BundleEntryError` on `bundle.partial_failures`). Unit logic proven; live end-to-end not. This is the riskiest T3 path (R1 from `da3d9aff`). Discharge when `get_batch` is callable in-session OR DSN/runbook exists. |
| T4, T5, T6, T7 | IN STRUCTURAL RATIFICATION | trios Reviewer-approved (`f67d548e`), batched structural-ratify requested (`2ec213ab`); T6 carries the SF-11 structural call (DA rules at T6 gate). NOT closed. |
| DO content (Area A, matrix+6 configs, escalation-seam/sealed-D1, demo sequence) | NOT STARTED | DO-owned; still the honest content-track gap |

T2-O1 and T3-O1 are now carried as **open obligations in the ledger** — not blockers (the DA was explicit they don't block downstream), but real debts to discharge, not absorbed.

## THE ESCALATION (PM / owner decision — the DA surfaced it, DO routes it)

The DA flagged a **systemic assurance ceiling**, and correctly framed it as a PM/owner decision rather than something the DA mandates. DO concurs and escalates it as a genuine intent/resourcing call:

**Three tasks now — T1's deferred integration, T2, T3 — have hit the identical wall:** integration suites are authored but have NEVER been canonically run, because the operator/DSN test-fixture wiring (a `MEM_MCP_TEST_DSN` test instance + operator runbook) does not exist. Each closes at lower assurance with a "canonical run owed" gap. T4–T7 will hit the same wall and close the same way unless this is resolved.

**The decision for PM/owner:** stand up the operator runbook + a `MEM_MCP_TEST_DSN` test instance ONCE, before T4–T7 close, so the back half of the spine closes at CANONICAL (full) assurance instead of accumulating lower-assurance closures.

DO's framing of the tradeoff (not a recommendation DO can make alone — it's resourcing + risk appetite, owner's call):
- **Do it now:** one-time operator setup cost; converts T4–T7 (and retroactively lets T2-O1/T3-O1 discharge) to full-assurance closure. Best if the PMO plugin is meant to be a durable production system, not just a demo prop.
- **Defer past the demo:** the Monday demo target (`d7a6c240`) is demo-by-SIMULATION of the PROCESS — it does NOT require canonical-tested plugin code to succeed. So lower-assurance closures are ACCEPTABLE for the demo specifically. The debt (T2-O1, T3-O1, and the T4–T7 equivalents) then sits in the ledger to discharge post-demo when the runbook is stood up.

DO's read: for the **demo**, defer is fine — the process integrity (surface-don't-absorb, honest gap-labeling) is itself the demo content, and the DA's VERIFIED-WITH-RECORDED-GAPS discipline is a GOOD thing to demonstrate, not a flaw. For the **product**, the runbook should be stood up before these obligations are forgotten. But which horizon governs is the owner's call — hence the escalation.

## WHAT DOES NOT CHANGE

- T4–T7 proceed to structural ratification regardless of this decision (the DA confirmed Step 3 unblocked; the obligations don't block).
- The demo can run on the simulation substrate at the current assurance level.
- DO's own content track (Area A, matrix+configs) is unaffected and remains DO's outstanding work.

## DO POSTURE / NEXT

DO has updated the ledger (this memo supersedes `fb94b12d` on T2/T3 state) and carries T2-O1 + T3-O1 open. Awaiting PM/owner on the operator-runbook horizon. Independently, DO's next authoring remains Area A → matrix+6 configs (the T6-blocking content) → answer Developer `e153cdb8`. None blocked.
