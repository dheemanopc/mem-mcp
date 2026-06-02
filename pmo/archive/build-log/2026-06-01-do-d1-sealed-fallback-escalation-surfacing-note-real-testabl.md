---
source: memsys (team: pmo)
id: ced035fd-e98f-4c22-b417-b8f2dd742110
type: decision
version: 1
is_current: True
created_at: 2026-06-01T05:36:30.364984Z
updated_at: 2026-06-01T05:36:30.364984Z
tags: [pmo, do-to-da, for-da, for-developer, d1-sealed-fallback, escalation-seam, do-content, pmo-project-pmo-v1-build, v1, current]
extracted_at: 2026-06-02
---

# DO — D1 SEALED-FALLBACK escalation-surfacing note (real, testable infra) → DA for composition-check

**Written 2026-06-01 by DO (PMO domain), threaded under manifest `75e8523c`. State: `for-da` (composition-check) / `for-developer` (reference). The sealed fallback for Decision D1 (escalation seam = Option B expose-during-demo, PM-blessed `2b256cad` Lock 2). This is the LAST DO content-track deliverable. Per PM bar: it must be REAL, TESTABLE infrastructure composed from existing PMO mechanisms — NOT a verbal "we'd figure it out." DO authors the content here; DA confirms it composes on the T2/T4/T5/T7 substrate.**

Refs: PM D1 lock + sealed-fallback-must-be-real bar `2b256cad` | DO master plan D1 `141a9f5e` | DA F4 ownership (DO authors note, DA composes) `7dcbb2c8` | Area A vocab `940cfbae` (tag set, reference-kinds, escalation ladder) | matrix+configs `238b450b` (escalation is a `working`-class resource for every role) | T2 write path | T7 registration/self-discovery `c9095015` | infra spec `7a9007f7`.

## THE GAP THIS SEALS (the original D1 seam mismatch)

The four role-tool specs deferred "how does an escalation SURFACE to the target role?" and three of them referenced a PM `pending_intake` list-key that didn't exist in the PM spec (which had `pending_ratifications` + `assigned_intake`). Option B (chosen) deliberately leaves the seam exposed so the demo can show the framework RECOVERING from its own spec gap. This note is the SEALED resolution applied ONLY if live recovery would otherwise STALL — so "expose" can never become "unrecoverable" (the only true failure mode per `d7a6c240`).

## THE FALLBACK CONTRACT (concrete, composed from existing mechanisms)

An escalation is surfaced to a target role by a write + a discovery query — no new mechanism, no memsys-core change. Three parts:

### 1. THE ESCALATION WRITE (uses the T2 working-write path)

An escalating role writes a working memory with this EXACT shape:
- `parent_id` = manifest root `75e8523c` (threaded; T2 path).
- `indexable=false`, `type=note` (working memory; Area A §7).
- Tags (the surfacing surface — this is the sealed contract that resolves the `pending_intake` mismatch):
  - the five base tags (Area A §7): `pmo`, `pmo-working`, `pmo-role-<author-role>`, `pmo-project-pmo-v1-build`, and working-type tag `pmo-escalation`.
  - **the routing tag**: `pmo-escalation-to-<target-role>` (e.g. `pmo-escalation-to-pm`, `pmo-escalation-to-da`). `<target-role>` is a role CODE from Area A §2 (pm/pa/dm/da/developer/reviewer). THIS is the canonical surfacing key — it replaces the three divergent `pending_intake`/`pending_ratifications`/`assigned_intake` references with ONE tag convention.
  - optional `pmo-escalation-open` (vs `pmo-escalation-resolved`) state tag, so resolved escalations drop out of the pending query (mirrors story-state convention, Area A §4).
- Body: the escalation content + a `references` edge of kind `responds-to` (Area A §3) citing the memory/work-item that triggered it, so the target can traverse to context.

### 2. THE SURFACING QUERY (uses the T7 self-discovery / list_memories path)

A role discovers escalations routed to it by the SAME self-discovery mechanism T7 already built — a tag-filtered `list_memories`:
- `tags = ["pmo-escalation", "pmo-escalation-to-<my-role>", "pmo-escalation-open"]`, `indexable=false`.
- Returns exactly the open escalations addressed to this role, newest-resolvable by `created_at`. This is the `default_list_query` surface — each role's pending-intake named query (`pm_pending_intake`, `da_pending_structural_ratifications`, etc. from `238b450b`) RESOLVES to this tag-filter. The named-query registry entry for intake = this escalation query. That ties the matrix's `default_list_query` names directly to this fallback, closing the loop.

### 3. RESOLUTION (write-fresh, no in-place)

The target role, on handling the escalation, writes a `responds-to` reply (its answer) AND the escalation is marked resolved by writing a fresh registration-style state flip (re-tag `pmo-escalation-resolved`); since working memories are write-fresh (no in-place update, SF-11 discipline), "resolved" is a new leaf citing the original, and the open-query stops returning it because the latest state leaf for that escalation carries `-resolved`. (Same recency-resolution pattern as T7 registration.)

## WHY THIS MEETS THE "REAL, TESTABLE" BAR (not paper)

Every element is an existing, verified mechanism — this composes, it does not invent:
- The write = T2 `write_working` (verified).
- The surfacing query = T7 `find_my_work_items`-style tag-filtered `list_memories(indexable=false)` (verified; SF-10 strict-AND tag filter).
- The trigger linkage = `references` `responds-to` edge = T5 reference-spine (verified).
- The open/resolved state = the story-state tag convention (Area A §4; pure convention, no engine enforcement).

**Testable assertions (the composition-check / eventual integration test):**
- T-D1-1: an escalation write lands with `pmo-escalation` + `pmo-escalation-to-<target>` + `pmo-escalation-open`; the target's surfacing query returns it; a NON-target role's query does NOT.
- T-D1-2: after resolution (fresh `-resolved` leaf), the open-query no longer returns it.
- T-D1-3: the `responds-to` edge resolves from escalation → trigger via `refs_out` (target can reach context).
- T-D1-4: the per-role `default_list_query` intake name (`238b450b`) resolves to exactly this tag-filter (ties matrix→fallback).

## ASK OF DA (composition-check, per F4 `7dcbb2c8`)

Confirm this fallback COMPOSES on T2/T5/T7 + the matrix's named-query registry — specifically that:
(a) the `pmo-escalation-to-<role>` routing tag is the single canonical surfacing key (resolving the original three-way `pending_intake` mismatch), and
(b) the per-role intake `default_list_query` names in `238b450b` should each resolve to the tag-filter in §2 — i.e. the six named resolvers T6 must implement INCLUDE this escalation query as their intake surface.
If composition holds, this seals D1: exposed by default, but a stall is always recoverable by applying this contract. If DA finds a gap in composition, surface it and DO revises (or, per `2b256cad`, if it can't be made to compose cheaply, D1 flips to Option A lock-first).

This is the final DO content-track item. With `e153cdb8` (resolved `bb55f05b`), Area A (`940cfbae`), and matrix+configs (`238b450b`), the DO content track is COMPLETE pending DA's composition-check here and on the six named resolvers.
