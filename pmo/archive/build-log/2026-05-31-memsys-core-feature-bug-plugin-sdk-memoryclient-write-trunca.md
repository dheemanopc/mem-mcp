---
source: memsys (team: pmo)
id: d287353a-99ec-40a1-ace0-b14164a4c25a
type: decision
version: 1
is_current: True
created_at: 2026-05-31T15:34:20.177217Z
updated_at: 2026-05-31T15:34:20.177217Z
tags: [current, feature-request, for-worker, infrastructure, memsys-core, pmo, pmo-project-pmo-v1-build, pmo-surfaced, project-manifest, sdk-substrate-gap, t2-dependency, v1]
extracted_at: 2026-06-02
---

# MEMSYS-CORE FEATURE/BUG — Plugin SDK `MemoryClient.write` truncates `parent_id` + `indexable`; extend to forward both

**Written 2026-05-31 by DA (PMO domain), threaded under manifest `75e8523c`. State: `pmo-surfaced` memsys-core gap, for owner-as-implementer. Surfaced from the T2 implementation blocker `25eab7f6`; DA direction `6d43a262` ruled option (A) extend-the-SDK. This memo is the standalone core work item. Owner indicated they will implement this directly while the PMO track proceeds to T3 in parallel. Sibling to core gaps `36ac16a1` (`on_startup` never invoked) and `edeae913` (plugin-onboarding-bootstrap).**

Refs: T2 impl blocker `25eab7f6` (full SDK-shape evidence) | DA direction option (A) `6d43a262` | SF-6 lock + SF-7 defer (in `6d43a262`) | T2 spec `4eb18941` | parallel core gaps `36ac16a1`, `edeae913`.

## TYPE
Feature gap in the Plugin SDK client surface. Not a substrate bug — the substrate (tool layer) already supports the fields; the SDK client does not forward them. So this is a surgical SDK-surface extension, not a schema or migration change. NO new memsys-core tables.

## CURRENT STATE (verified from memsys-core HEAD, per `25eab7f6`)
- `src/mem_mcp/plugins/contract.py` (~line 101) — `MemoryClient` Protocol `write` exposes only: `content` (positional), `type`, `tags`, `metadata`. Returns `UUID`.
- `src/mem_mcp/plugins/clients/memory.py` (~line 23) — concrete `MemoryClientImpl.write` forwards exactly those four into `MemoryWriteInput`. Does NOT pass `parent_id`, `indexable`, `references`, `visibility`, `expires_at`, `team_id`.
- `src/mem_mcp/mcp/tools/write.py` (~line 71) — the underlying `MemoryWriteInput` ALREADY has `parent_id: UUID | None = None` (~line 79) and `indexable: bool = True` (~line 83); flat-threading validation + `indexable` column propagation are wired (~lines 363-403, 444, 563).
- Net: substrate supports threading + indexable; the Plugin SDK client is the truncation point.

## WHY IT MATTERS (impact)
Every PMO working-memory write needs `parent_id=manifest_root` (thread under the manifest) and `indexable=False` (keep working memories out of semantic search). Without these on the SDK surface, plugin writes land top-level and searchable — the inverse of the PMO working-memory design (PM ruling `ba6d113a`, infra spec D3 `7a9007f7`). Blocks T2 end-to-end; T6 and T3 (production paths and/or fixtures) need the same surface.

## REQUIRED CHANGE (option A, surgical)
1. **`contract.py` — `MemoryClient` Protocol `write`:** add two keyword-only params:
   - `parent_id: UUID | None = None`
   - `indexable: bool = True`
2. **`clients/memory.py` — `MemoryClientImpl.write`:** accept the same two params and forward them straight into `MemoryWriteInput(content=, type=, tags=, metadata=, parent_id=, indexable=)`. No transformation; the tool layer already validates flat-threading and propagates `indexable`.
3. Defaults preserve back-compat: existing callers passing none get `parent_id=None` + `indexable=True` — identical to today's behavior. No existing plugin (kite/reminders) breaks.

## ACCEPTANCE (definition of done for THIS core item)
- Protocol + impl both expose `parent_id` and `indexable` as keyword-only with the defaults above.
- A plugin-side write with `parent_id=<root>` lands as a flat leaf under that root (thread-get shows it; `parent_id` correct in storage).
- A plugin-side write with `indexable=False` is excluded from `memory_search` and confirmed `indexable=False` in storage.
- Existing kite/reminders plugin writes unaffected (defaults = old behavior).
- Lands on memsys-core prod (the live `/opt/mem-mcp` revision the PMO plugin installs against).

## SCOPE BOUNDARY (explicit, per DA direction `6d43a262`)
- IN: `parent_id` + `indexable` on the SDK `write` surface only.
- OUT (deliberately): `references` on the SDK write surface. That is the SF-7 deferred question — T5's lineage mechanism (`references` vs `memory_supersede`) is decided at T5 plan time, NOT pre-committed here. Do not add `references` to the SDK in this change unless T5 design later calls for it.
- OUT: any new table, migration, visibility/team-scope param. Strictly the two fields.

## OWNERSHIP / SEQUENCING
- Owner implements directly (not authored as a PMO-plugin-repo PR — this is core, not plugin code, per repo-placement discipline `50e11ec8`).
- Runs in PARALLEL with PMO-track T3. T2 implementation is BLOCKED on this item landing (see sibling T2-blocked status memo referencing this one).
- On landing: SF-6 (locked in `6d43a262`) describes the now-live surface; T2 implements unchanged against it; DA confirms the live surface at T2 verification.
- Priority: this is on T2's critical path → higher urgency than `36ac16a1` / `edeae913` (which are T7/Area-D dependencies, not blocking the current task). Final sequencing is owner/PM's call.
