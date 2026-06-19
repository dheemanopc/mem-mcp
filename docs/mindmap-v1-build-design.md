# Mind-Map v1 — Build Design

## Status

`proposed` — 2026-06-19. Awaiting owner review before code.

Turns the functional model in `memsys-mindmap-spec-v0.1` (graduated KB,
spec index `c082d427`, status *functional-model-closed, NOT build-blessed*)
into a buildable technical design. Target: **a working system for real use**,
all seven graduated decisions implemented to a usable state.

This doc resolves only the build-blocking choices — the core/plugin seam and
the schema. The lifecycle protocol itself is deliberately kept as swappable
prompt assets (per spec node `0aa54585`), so it is not frozen here.

## Source decisions (the spec body)

| Node | Decision |
|---|---|
| `6844c142` | Map lifecycle, exclusive ownership, no-reopen, seed-from-spec, graduation-with-confirm, archived-but-referenceable, API contract |
| `f5e52f08` | Open-loop ownership = whose-turn |
| `d1ebfc6e` | Promotion = new KB write referencing archived node; **NOT** supersession |
| `5f439cd0` | Atomicity via byte-limit; split-not-trim; sticky-until-reviewed |
| `526ca485` | Ratification = citation-backed + endorsement-strength gradient |
| `6af0f365` | Reviewer = structural smoke-detector, map-only, write-counter trigger, advisory |
| `0aa54585` | Build order = build-concurrently; keep driver/reviewer swappable; observability day one |

## Founding constraints (carried from spec, non-negotiable)

1. **Memsys has no intelligence by design.** Storage/links/filters/search only.
   All judgment lives in the model + human above it. Tools enforce; they never decide.
2. **System of thought, not record.** Verification catches *mistakes, not cheating*.
3. **Additive / non-breaking.** Old flat memories = unmapped nodes, untouched.
4. **No compression-as-truncation.** Split, never trim.

## The core/plugin seam (the load-bearing decision)

Per spec node `507bab07` ("provenance *vocabulary* lives in core; *protocols*
that animate it are plugins") and the build-order warning in `0aa54585`
(storage firm, judgment rippable):

| Layer | Lives in | Owns |
|---|---|---|
| **Core** (nouns) | `mem_mcp` core | node-type vocabulary; typed edges; provenance metadata |
| **Plugin `mindmap`** (verbs) | new plugin schema `plugin_mindmap` | map containers, exclusive ownership, active-map, write-counter, archived state, the lifecycle tools, driver + reviewer **prompt assets** |

Rationale: a judgment flaw baked into trusted core is the expensive mistake;
storage can be ripped out and redone cheaply. So only the smallest, settled
vocabulary change touches core; everything mutable/judgmental is plugin-side
and swappable.

### Core changes (minimal, additive)

The current memory `type` enum is exactly
`note | decision | fact | snippet | question`
(`alembic/versions/0001_initial_schema.py`, the `memories.type` CHECK).

**Change 1 — extend the type enum** with `position` and `challenge`
(the thinking-node vocabulary the spec wants first-class; already an agreed
owner decision — provenance vocab belongs in core, not a plugin).
- Migration: new Alembic revision that rewrites the `memories.type` CHECK
  constraint to add the two values. Purely additive; no row rewrites.
- `position` and `challenge` are **working** thinking-nodes → they join the
  *non-versioned, edit-in-place* group (note/snippet/question). They do **not**
  go in `VERSIONED_TYPES` (`memory/versioning.py`); only `decision`/`fact`
  stay versioned. This extends ADR-0005; a short ADR-0007 will record it.
- Update input validators (`mcp/tools/write.py`), recency tuning
  (`memory/recency.py` — thinking-nodes should decay like questions), and the
  tool-description enum text.

**No other core change.** Specifically we reuse, untouched:
- `memory_references` (table, `0025`) for **all** edges — `reference_kind` is
  open-text, so the map edge vocabulary needs zero schema work.
- `metadata` JSONB for provenance fields (`engagement`, `node_role`,
  `ratification_strength`, `ratification_citation`, trajectory).
- supersession/versioning chain (kept *separate* from promotion — see below).

### Plugin `mindmap` (new)

Mirrors the `reminders` plugin (entry-point discovery, own Postgres schema,
own Alembic migrations, `MemoryClient` SDK for node I/O, RBAC permissions,
`surface_pending_state` hook). Nodes are core memories written via
`MemoryClient`; the plugin schema stores only the **map graph + lifecycle
state**, never node content.

**Plugin tables (`plugin_mindmap` schema):**

```
maps
  id              uuid pk
  tenant_id       uuid            -- RLS scope, mirrors core
  title           text
  state           text  check (state in ('live','archived'))   default 'live'
  seed_spec_node  uuid null       -- origin spec node for from_spec maps
  writes_since_review  int        default 0   -- reviewer write-counter
  graduated_spec_index uuid null  -- KB spec-index node produced at close
  created_at      timestamptz
  closed_at       timestamptz null

map_membership                    -- exclusive ownership; a memory in <=1 row
  map_id          uuid  fk -> maps(id)
  memory_id       uuid            -- the core node id (no FK; cross-schema)
  node_role       text            -- root_question | position | challenge | decision | ...
  added_at        timestamptz
  primary key (memory_id)         -- enforces "owned by EXACTLY ONE map"

active_map                        -- which map owns new writes, per conversation
  tenant_id       uuid
  conversation_id text            -- source_client / session key
  map_id          uuid  fk -> maps(id)
  primary key (tenant_id, conversation_id)
```

`map_membership.primary key (memory_id)` is what makes exclusive ownership a
**structural guarantee**, not a convention — the per-membership-state problem
is avoided, not deferred. A memory with no membership row = unmapped (facts,
old flat memories) → constraint #3 satisfied for free.

**Plugin tools** (`mindmap_<verb>`):

| Tool | Maps to spec | Behaviour |
|---|---|---|
| `mindmap_open` | `new_map(cold)` / `new_map(from_spec)` | cold: similarity-search **live maps only**, return ranked "revisit?" candidates, never auto-merge. from_spec: **exact** dedup guard (`refs_in` of the spec node filtered to live maps). Sets the new map active. |
| `mindmap_set_active` | active-map switch | explicit act; subsequent node writes are owned by it |
| `mindmap_write_node` | owned-node capture | writes a core memory (type position/challenge/decision/question), inserts membership into the active map, bumps `writes_since_review` |
| `mindmap_link` | typed edge | inserts into core `memory_references` with a map `reference_kind` |
| `mindmap_close` | `close_map` | runs graduation (below), then sets `state='archived'` |
| `mindmap_review` | reviewer | runs the map-only reviewer pass, clears the sticky flag |
| `mindmap_graduate` | promotion step | called inside close; can be dry-run for the propose step |

**Reference-kind vocabulary** (open-text values written to `memory_references`):
`displaced-from`, `resolves-under`, `dropped-under`, `superseded-under`,
`open-under`, `principle-under`, `promoted-from` (graduation backlink),
`seeded-from` (map ← spec node). Extensible; the core needs no awareness.

**Prompt assets (swappable, not core):** `driver.md`, `reviewer.md` shipped in
the plugin package and loaded at runtime. In Claude-Code/agent contexts the
reviewer runs as a **sub-agent**; in chat the main agent loads `reviewer.md`
itself (per `6af0f365`). Replacing judgment = editing a prompt file.

## Lifecycle → mechanism mapping

```
KB/spec --seed--> mindmap_open(from_spec)      [seeded-from edge, map.live]
        --brainstorm--> mindmap_write_node*     [owned nodes, counter++]
        --(every N writes)--> sticky review flag [surface_pending_state]
        --"we're done"--> mindmap_close:
              1. propose significant decisions (model judgment)
              2. HUMAN confirms                (the second human touch-point)
              3. for each confirmed: NEW KB memory_write (type=decision)
                 with a `promoted-from` reference to the archived node,
                 carrying trajectory-summary + ratification metadata
              4. write spec-index node; set maps.graduated_spec_index
              5. maps.state = 'archived'
```

**Promotion is a new write, never supersession** (`d1ebfc6e`): graduation calls
`MemoryClient.write`, *not* `supersede`. The two stay separate — supersession is
"a fact got corrected"; promotion is "a KB node born from archived reasoning."

**Archived state** is excluded from `mindmap_open` similarity search (finished
maps never nag as "revisit?") but **included** in reference traversal
(`refs_out`/`refs_in` still walk into it), so a deep-dive on a spec line
recovers full reasoning. Archived = cold, not deleted, not compressed.

**Two human touch-points only:** `mindmap_open` ("let's brainstorm") and
confirm-at-close. Everything between is silent model capture.

## The other four decisions

- **Whose-turn (`f5e52f08`):** each open node carries `metadata.responsible_party`
  (`owner` | `model`). `surface_pending_state` returns the open set, mostly
  "waiting on owner" — that's the resume/graduation surface. Reviewer backstops
  mis-assigned turns.
- **Atomicity / split-not-trim (`5f439cd0`):** core `content` is already capped
  at 32,768 chars. The plugin adds a **softer** byte threshold on
  `mindmap_write_node`: past it, the node is flagged "looks like >1 decision";
  the action is **split into linked nodes**, never compress. Flag is sticky
  until a reviewer clears it.
- **Ratification gradient (`526ca485`):** `mindmap_write_node` / a ratify call
  records `metadata.ratification_strength` ∈
  `{survived-challenge, explicit-endorse, delegate, tacit}` plus
  `metadata.ratification_citation` (the owner utterance, deposited into the node
  so the structure is self-contained). Strength drives graduation:
  endorse/survived-challenge promote clean; **delegate** promotes tagged
  "verify substance" (the dangerous-to-mislabel one); tacit does not auto-promote.
- **Reviewer (`6af0f365`):** map-only (no transcript by design — threat model is
  *mistakes, not cheating*). Trigger = `writes_since_review >= N`; raises a
  sticky flag via `surface_pending_state`, never force-interrupts. Advisory:
  surfaces flags to the driver; human-confirm-at-graduation is the final backstop.

## Observability (day one, per `0aa54585`)

Every ratification, fork, promotion, split-flag, and reviewer-flag writes a row
to a `plugin_mindmap.judgment_log` (append-only: map_id, node_id, event,
actor, payload, ts) for owner review. Edge-case judgment failures must be
*visible, not silent* — the afternoon self-test only exercised easy cases.

## Open loops carried (do NOT treat as closed)

- `890f7e47` **In-flight live capture is UNTESTED** — load-bearing. Slice 4
  exercises it deliberately and early; only post-hoc capture has passed.
- `b2e23042` Multi-person/team — deferred. Schema mirrors core tenant/team
  scoping so a later `visibility='team'` extension is non-breaking, but v1 is
  single-author.
- `ad5372db` Fact mechanics — undefined. v1 keeps facts unmapped/ownerless
  (no membership row); no new fact behaviour.

## Build slices (sequenced; risk early)

| # | Slice | Done when |
|---|---|---|
| 1 | Core: extend type enum (`position`,`challenge`) + validators + recency + ADR-0007 | migration up/down green; write/get of a `position` node round-trips; non-versioned confirmed |
| 2 | `mindmap` plugin skeleton | entry-point discovered; `plugin_mindmap` schema + tables migrate; config + permissions declared; `judgment_log` live |
| 3 | Lifecycle spine: `mindmap_open` → `set_active` → `write_node` → `close` | one map opens, owns nodes exclusively, closes/archives; ownership PK enforced by test |
| 4 | **Live in-flight capture** (`890f7e47`) | a running conversation captures owned nodes into the active map turn-by-turn, not post-hoc; documented as passed/failed |
| 5 | Edges + `open` similarity-surface (live-only) + `from_spec` exact dedup | candidates surfaced not merged; dedup guard blocks duplicate from_spec maps |
| 6 | Graduation: propose → confirm → promote-with-backlink → archive | confirmed decisions become KB nodes referencing archived originals; archived excluded from search, included in traversal |
| 7 | Reviewer + ratification gradient + atomicity split-flag | counter trigger raises sticky flag; reviewer pass clears it; gradient + split-flag recorded and surfaced |

## Decisions needed from owner at review

1. **Type-enum extension in core** — confirm `position`+`challenge` go in the
   core enum (vs. faked as `note` + `metadata.node_role` in the plugin). The
   spec says core; confirming because it's the one irreversible core change.
2. **`conversation_id` source** — what keys "the active map for the
   conversation"? Proposed: the MCP `source_client_id` / session. Needs a real
   handle from the client side.
3. **Reviewer trigger N** — initial writes-since-review threshold (proposed: 10;
   it's an empirical knob per the spec's residual-tension note).
4. **Single-author v1** — confirm team/multi-person stays deferred (`b2e23042`).
