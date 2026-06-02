---
source: memsys (team: pmo)
id: 27824fcb-362e-44c7-bc2a-f0094d61ccba
type: decision
version: 1
is_current: True
created_at: 2026-05-31T15:34:54.040271Z
updated_at: 2026-05-31T15:34:54.040271Z
tags: [current, da-to-developer, da-to-do, for-developer, for-do, infrastructure, memsys-core, pmo, pmo-project-pmo-v1-build, pmo-task-T2, project-manifest, t2-blocked, v1]
extracted_at: 2026-06-02
---

# T2 STATUS — BLOCKED on memsys-core SDK extension `d287353a`. Paused, ratified, resumes when the fix lands.

**Written 2026-05-31 by DA (PMO domain), threaded under manifest `75e8523c`. State: `for-developer` / `for-do`. Marks T2 implementation BLOCKED on the memsys-core feature/bug `d287353a` (SDK `write` parent_id+indexable extension). T2 DESIGN remains ratified (`1e24baec`); only implementation is paused. PMO track proceeds to T3 in parallel per PM/owner direction this session.**

Refs: blocking item → memsys-core gap `d287353a` | T2 impl blocker `25eab7f6` | DA direction option (A) `6d43a262` | DA T2 structural ratification `1e24baec` | T2 trio Plan `780ea619` / LLD `2d05c14e` / Test Plan `542b1c74`.

(Structured `references[]` omitted — write-time validation rejected the inbound ref to the just-written core-gap memory `d287353a` due to async index drain; the blocked-by linkage is carried in-body + via the `t2-blocked` / `memsys-core` tags. Linkage holds.)

## T2 STATE
- **BLOCKED-ON:** `d287353a` (core SDK extension). T2 cannot complete its DoD end-to-end through `ctx.memories` until that lands.
- **DESIGN STATUS:** RATIFIED, unchanged. The T2 trio is correct as written; no v2. When the SDK exposes `parent_id` + `indexable` (SF-6 surface), T2 implements against it unchanged.
- **IMPLEMENTATION TREE:** held at T1's last commit. No bending code written (no metadata-smuggle, no SDK bypass) — confirmed in `25eab7f6`.
- **NOT ABANDONED, NOT REORDERED OUT:** T2 still precedes T6 in the dependency spine (T6 consumes T2). Starting T3 first fills the wait productively; it does not change that T6 needs T2.

## RESUME CONDITION
`d287353a` lands on memsys-core prod → Developer implements T2 per the ratified trio → `awaiting-verification` impl-response (confirms SF-6 surface live + reports any residual signature detail) → DA verification ratification → PM milestone closure.

## PARALLEL TRACK
T3 (manifest schema + session-load) proceeds NOW. T3 is read-path (memory_get_batch + memory_thread_get); its production code does not touch the write gap. Its test fixtures that seed `indexable=false` threaded leaves will seed via the MCP tool layer (which already supports both fields), so T3 is fully buildable and testable in parallel with zero rework when the SDK extension lands. See T3 kickoff (sibling memo this session).
