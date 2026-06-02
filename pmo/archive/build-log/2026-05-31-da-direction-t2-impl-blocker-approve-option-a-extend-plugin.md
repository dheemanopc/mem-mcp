---
source: memsys (team: pmo)
id: 6d43a262-8f4d-444e-98da-ece3c09a557f
type: decision
version: 1
is_current: True
created_at: 2026-05-31T15:11:20.710803Z
updated_at: 2026-05-31T15:11:20.710803Z
tags: [current, da-to-developer, da-to-do, for-developer, for-do, infrastructure, memsys-core, pmo, pmo-project-pmo-v1-build, pmo-task-T2, project-manifest, sdk-substrate-facts, sdk-substrate-gap, t2-impl-blocker, v1]
extracted_at: 2026-06-02
---

# DA DIRECTION — T2 impl blocker: APPROVE option (A), extend Plugin SDK. T2 pauses on a memsys-core gap. SF-6 locked, SF-7 deferred.

**Written 2026-05-31 by DA (PMO domain), threaded under manifest `75e8523c`. State: `for-developer` / `for-do`. Resolves the Developer's T2 implementation blocker `25eab7f6` (R2 carry-forward landing). This is the cross-task structural call the carry-forward pattern routed to DA. Verdict: option (A) — extend the Plugin SDK; T2 design stays ratified as-is; T2 implementation PAUSES on a filed memsys-core gap.**

Refs: Developer blocker `25eab7f6` | DA T2 structural ratification `1e24baec` (the carry-forward instruction that produced this) | T2 LLD v1 `2d05c14e` (R2 mitigation) | T2 spec `4eb18941` | SF-1..SF-5 source `584614ac` | parallel core gap `on_startup` `36ac16a1` | core feature-request pattern `edeae913`.

## DIAGNOSIS CONFIRMED (independent cross-check, not taken on report alone)
The Developer's finding holds and is corroborated by this session's own evidence:
- The PMO substrate-facts block (`4eb18941`, verified against memsys this session) states `parent_id` threading and `indexable=false` BOTH work at the substrate/tool layer. Every working memory written this session (checkpoint, escalations, this thread) used `parent_id` + `indexable` successfully THROUGH THE MCP TOOL LAYER.
- The Developer's claim is exactly consistent: the SUBSTRATE (`MemoryWriteInput` in `mcp/tools/write.py` — `parent_id` line 79, `indexable` line 83, threading + indexable propagation wired) supports both; the **Plugin SDK client (`MemoryClient` Protocol `contract.py:101` + `MemoryClientImpl` `clients/memory.py`) TRUNCATES them**, forwarding only `content, type, tags, metadata`.
- Net: the gap is real and is at the SDK-CLIENT layer, not the tool layer. This is not a T2 design error — the T2 trio targeted the right shape; the SDK surface it must call through does not yet expose the fields.

## WHY THIS IS STRUCTURAL / CROSS-TASK (DA scope, correctly routed)
`parent_id` (threading under the manifest) and `indexable=False` (keeping working memories out of semantic search) are load-bearing for EVERY working-memory write across the system — T2 helpers, T6 generic engine, T3 session writes. Without them, working memories land top-level and searchable: the exact inversion of the PMO working-memory design (PM ruling `ba6d113a` + infra spec D3). So this cannot be resolved task-locally; it is a substrate-surface gap that gates the whole working-memory mechanism.

## VERDICT — OPTION (A): EXTEND THE PLUGIN SDK
Add `parent_id: UUID | None = None` and `indexable: bool = True` to BOTH the `MemoryClient` Protocol (`contract.py`) and `MemoryClientImpl.write` (`clients/memory.py`), the impl forwarding them straight into `MemoryWriteInput`. Rationale:
- It is the only option that preserves the SDK abstraction AND T2's DoD with no shrink.
- T6, T3, and (partly) T5 inherit the correct write surface — the fix pays forward across the spine, it is not a T2-local patch.
- SF-5 discipline is preserved: the helpers still touch the SDK only through the existing `TYPE_CHECKING`-guarded boundary; no new SDK leak.

**(B) metadata-smuggle — REJECTED (DA concurs with Developer).** Still requires a memsys-core change AND couples the plugin to a hidden transport convention; strictly worse than (A).
**(C) SDK bypass — REJECTED (DA concurs with Developer).** Discards the RBAC scoping the SDK exists to provide; would force T6 to bypass too; it is precisely the anti-pattern the Plugin SDK (Phase 2.5) was introduced to eliminate.

## OWNERSHIP + SEQUENCING (the one refinement DA adds to the Developer's rec)
This is a **memsys-core change, NOT a PMO change.** Therefore:
1. **File it as a memsys-core gap/feature request**, sibling to the `on_startup` gap `36ac16a1`, in the same PMO-surfaced core-gap queue (`edeae913` pattern). Title direction: "Plugin SDK `MemoryClient.write` truncates `parent_id` + `indexable` — extend Protocol + impl to forward both into `MemoryWriteInput`."
2. **The PMO track does NOT author this as a smuggled-in PMO PR.** Per the T1 repo-placement discipline (`50e11ec8`) the PMO plugin repo stays mechanism-only and does not modify memsys-core opportunistically. If the owner later wants the Developer to author the core PR explicitly (scoped, reviewed as a core change), that is a separate authorization — flagging it as an option, not assuming it.
3. **T2 implementation PAUSES until the SDK extension lands.** The T2 trio (Plan `780ea619` / LLD `2d05c14e` / Test Plan `542b1c74`) STAYS RATIFIED as written — no v2 needed, because the design was correct; only the substrate it targets needs to expose the fields. When the extended SDK is live, T2 implements unchanged against it.
4. **Developer: do NOT write any bending code** (no metadata-smuggle, no bypass) — which you have correctly already not done. Hold the implementation tree at T1's last commit until the core gap closes.

## SUBSTRATE-FACT ADDENDUM — SF-6 LOCKED, SF-7 DEFERRED
Folding into the SF block carried into T3..T7:

- **SF-6 — LOCKED (effective once the option-(A) extension lands):** `MemoryClient.write` accepts `content, type, tags, metadata, parent_id, indexable`. Working memories ALWAYS pass `parent_id=manifest_root` + `indexable=False`. Formal artifacts (T5/T6) pass `parent_id=None` + leave `indexable=True`. Until the extension lands, SF-6 describes the TARGET surface, not the current one — any task planning against it before it ships must note the dependency.

- **SF-7 — FLAGGED, NOT LOCKED (confirm at T5 plan time):** `MemoryClient.write` does NOT expose `references` (formal-artifact derivation links). DA does NOT pre-commit T5's lineage mechanism now — whether T5 needs a parallel SDK extension for `references` or uses `memory_supersede` is a T5 design decision, to be made when T5 is planned, not pre-empted here. Recorded so T5 inherits the open question rather than a silent assumption.

## DO — TRACKING (routed `for-do`)
The memsys-core gap queue now has THREE PMO-surfaced items: `edeae913` (plugin-onboarding-bootstrap), `36ac16a1` (`on_startup` never invoked), and THIS one (SDK write-field truncation). DO sequences core-gap closure when convenient per the master plan; this one is on T2's critical path, so it has higher urgency than the other two (which are T7/Area-D dependencies, not blocking the current task). Flagging the priority ordering for DO's awareness; the sequencing call is DO/PM's.

## T2 GATE CHAIN — STATUS
kickoff `e47f81b2` → Reviewer approve `b7a2742c` → DA structural ratify `1e24baec` → impl started → **impl blocker surfaced + DA direction (this memo): PAUSE on core gap, option (A).** Resumes: core SDK extension lands → Developer implements T2 unchanged → `awaiting-verification` impl-response (now also confirming SF-6 surface live) → DA verification ratification → PM milestone closure.

Developer: file the core gap as directed; hold T2 implementation; SF-6 locked / SF-7 deferred as above. DA available for the gap's framing if useful.
