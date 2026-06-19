# Mind-Map v1 — Build Design

## Status

`approved-for-build` — owner-reviewed 2026-06-19. Implementing now.

Turns the functional model in `memsys-mindmap-spec-v0.1` (functional-model-closed,
spec index `c082d427`) into the buildable design. Goal: **a fully functional
system, up and running** — all seven graduated decisions implemented and usable.

### Owner decisions at review (override the earlier draft)

1. **No plugin. Everything lives in core.** The earlier core/plugin seam is
   dropped. Node vocabulary, map containers, lifecycle tools, reviewer — all in
   `src/mem_mcp` core.
2. **A map's root *is* a memsys key.** A map is rooted at a memory and addressed
   by that root memory's slug (the memsys "key"). No opaque map-id registry; the
   map handle = the root node's key.
3. **One map per conversation is convention, not design.** Working on a single
   map at a time is how the driver *uses* the system; it is **not** enforced.
   Map ops take the map key explicitly — no mandated active-map lock, no
   conversation-id plumbing.
4. **Reviewer threshold N = 15** to start (10 is too eager). Tunable.
5. **Team / multi-person deferred** (`b2e23042`).

## Source decisions (the spec body)

| Node | Decision |
|---|---|
| `6844c142` | Map lifecycle, exclusive node ownership, no-reopen, seed-from-spec, graduation-with-confirm, archived-but-referenceable, API contract |
| `f5e52f08` | Open-loop ownership = whose-turn |
| `d1ebfc6e` | Promotion = new KB write referencing archived node; **NOT** supersession |
| `5f439cd0` | Atomicity via byte-limit; split-not-trim; sticky-until-reviewed |
| `526ca485` | Ratification = citation-backed + endorsement-strength gradient |
| `6af0f365` | Reviewer = structural smoke-detector, map-only, write-counter trigger, advisory |
| `0aa54585` | Build order = build-concurrently; keep driver/reviewer swappable; observability day one |

## Founding constraints (carried from spec, non-negotiable)

1. **Memsys has no intelligence by design.** Storage/links/filters/search only.
   All judgment lives in the model + human above it. Tools enforce; never decide.
2. **System of thought, not record.** Verification catches *mistakes, not cheating*.
3. **Additive / non-breaking.** Old flat memories = unmapped nodes, untouched.
4. **No compression-as-truncation.** Split, never trim.

## What a "map" is, concretely

A map is **a root memory plus the nodes that hang off it**. No separate opaque
identity:

- **Root node** = a memory (the root question/topic) given a **slug**, so the
  map's memsys key *is* that slug (owner decision 2). The slug vocabulary
  (`slugs.resource_type`, currently `decision|fact`) is extended to allow `map`.
- **Map record** = one row in `memory_maps` keyed by `root_memory_id`, holding
  the lifecycle state that isn't node content (live/archived, write-counter,
  seed origin, graduated index).
- **Member nodes** = positions / challenges / decisions captured under the map,
  recorded in `memory_map_membership` (exclusive: a node belongs to ≤1 map).
- **Edges** = rows in the existing `memory_references` table (open-text
  `reference_kind` — zero schema change for the edge vocabulary).

The driver passes the **map key (root slug)** explicitly to each map tool. There
is no enforced active-map; "one map per conversation" is driver discipline.

## Core changes

### 1. Node-type vocabulary (additive)

The current `memories.type` CHECK is
`note | decision | fact | snippet | question`. Add `position` and `challenge`
(first-class thinking nodes). Both are **working** nodes → non-versioned,
edit-in-place (they join note/snippet/question; only `decision`/`fact` stay in
`VERSIONED_TYPES`). Extends ADR-0005 → recorded as ADR-0007.

Touch points: the type CHECK migration; write validator(s); recency tuning
(thinking-nodes decay like questions); tool-description enum text.

### 2. Slug vocabulary (additive)

Extend `slugs.resource_type` CHECK to allow `map`, so a map root can be minted a
stable slug via the existing slug machinery (`teams/slugs.py` retry-suffix
loop). The map key the driver uses = this slug.

### 3. New core tables (public schema, RLS like peers)

```
memory_maps
  root_memory_id        uuid pk references memories(id) on delete cascade
  tenant_id             uuid not null
  title                 text not null
  state                 text not null check (state in ('live','archived')) default 'live'
  writes_since_review   int  not null default 0
  review_threshold      int  not null default 15          -- owner: N=15
  seed_spec_memory_id   uuid null                          -- origin for from_spec maps
  graduated_index_id    uuid null                          -- spec-index node minted at close
  created_at            timestamptz not null default now()
  closed_at             timestamptz null

memory_map_membership                                       -- exclusive ownership
  memory_id        uuid pk references memories(id) on delete cascade
  root_memory_id   uuid not null references memory_maps(root_memory_id) on delete cascade
  node_role        text not null      -- root | position | challenge | decision | ...
  added_at         timestamptz not null default now()

memory_map_events                                           -- observability, day one
  id               bigserial pk
  root_memory_id   uuid not null references memory_maps(root_memory_id) on delete cascade
  memory_id        uuid null
  event            text not null      -- ratify | fork | promote | split_flag | review_flag | open | close
  actor            text not null      -- model | owner | reviewer
  payload          jsonb not null default '{}'
  created_at       timestamptz not null default now()
```

`memory_map_membership` PK on `memory_id` makes **exclusive node ownership a
structural guarantee** — a node physically cannot belong to two maps. No
membership row = unmapped (facts, old flat memories) → constraint #3 free.

Each table follows the peer RLS pattern (ENABLE RLS, tenant-scoped policy,
`GRANT ... TO mem_app`) used by `memory_references`/`slugs`.

**No change** to: `memory_references`, `metadata` JSONB (carries
`responsible_party`, `ratification_strength`, `ratification_citation`,
trajectory), versioning/supersession chain (kept separate from promotion).

## Tool surface (core MCP tools, `mindmap_*`)

| Tool | Spec | Behaviour |
|---|---|---|
| `mindmap_open` | `new_map(cold\|from_spec)` | mint root memory (type `question`) + slug + `memory_maps` row. cold: similarity-search **live maps only**, return ranked "revisit?" candidates, never auto-merge. from_spec: take a source memsys key, add `seeded-from` edge, **exact** dedup guard (refuse a 2nd live map already seeded from the same source). Returns map key. |
| `mindmap_write_node` | owned capture | takes **map key** + content + `node_role` (position/challenge/decision) + optional ratification + optional links; writes a core memory, inserts exclusive membership, bumps `writes_since_review`, logs event. Past a soft byte threshold → returns a **split-suggested** flag (split into linked nodes, never trim). At `writes_since_review >= review_threshold` → returns a sticky `review_due` flag. |
| `mindmap_link` | typed edge | insert into `memory_references` with a map `reference_kind` |
| `mindmap_get` | resume/inspect | return the map graph: root, member nodes, edges, open loops (whose-turn), pending flags, state |
| `mindmap_review` | reviewer | run the map-only structural pass (advisory), surface flags, reset `writes_since_review` to 0 |
| `mindmap_close` | `close_map` + graduation | propose significant decisions → **human confirms** → for each: new KB `memory_write` (type `decision`) with a `promoted-from` edge to the archived node, carrying trajectory + ratification metadata → mint spec-index node → `state='archived'`. |

**Reference-kind vocabulary** (open-text in `memory_references`):
`displaced-from`, `resolves-under`, `dropped-under`, `superseded-under`,
`open-under`, `principle-under`, `promoted-from`, `seeded-from`. Extensible.

**Driver / reviewer prompts** ship as swappable asset files in the repo
(`skills/` or a `prompts/` dir) loaded at runtime — replacing judgment = editing
a prompt, never core code (`0aa54585`, `6af0f365`).

## The seven decisions → mechanism

- **Lifecycle / ownership / archived (`6844c142`)** — tables above; archived maps
  excluded from `mindmap_open` similarity search but still walkable via
  `refs_in`/`refs_out` (cold, not deleted, not compressed). Two human
  touch-points only: open ("let's brainstorm") and confirm-at-close.
- **Whose-turn (`f5e52f08`)** — open nodes carry `metadata.responsible_party`
  (`owner`|`model`); `mindmap_get` returns the open set as the resume surface.
- **Promotion not supersession (`d1ebfc6e`)** — close calls
  `MemoryClient.write`, never `supersede`. Separate concepts, kept separate.
- **Atomicity / split-not-trim (`5f439cd0`)** — soft byte threshold on
  `write_node` raises a sticky split-suggested flag; the action is split into
  linked nodes, never compress; sticky until a review clears it.
- **Ratification gradient (`526ca485`)** — `metadata.ratification_strength` ∈
  `{survived-challenge, explicit-endorse, delegate, tacit}` +
  `ratification_citation` (the owner utterance, deposited into the node so the
  structure is self-contained). Drives graduation: endorse/survived promote
  clean; **delegate** promotes tagged "verify substance"; tacit no auto-promote.
- **Reviewer (`6af0f365`)** — map-only (threat model is *mistakes, not cheating*,
  so no transcript). Trigger = `writes_since_review >= 15`; raises a sticky
  `review_due` flag, never force-interrupts. Advisory; human-confirm-at-close is
  the final backstop.
- **Build order + observability (`0aa54585`)** — slices below; every ratify /
  fork / promote / split-flag / review-flag writes a `memory_map_events` row.

## Open loops carried (NOT closed)

- `890f7e47` **In-flight live capture is UNTESTED** and load-bearing — exercised
  deliberately in slice 4, early.
- `b2e23042` Team/multi-person — deferred; tables mirror tenant scoping so a
  later `visibility='team'` is non-breaking.
- `ad5372db` Fact mechanics — undefined; v1 keeps facts unmapped/ownerless.

## Build slices (sequenced; risk early)

| # | Slice | Done when |
|---|---|---|
| 1 | Node + slug vocabulary: enum `position`/`challenge`, slug `map`, validators, recency, ADR-0007 | migration up/down green; a `position` round-trips; non-versioned confirmed |
| 2 | Tables: `memory_maps`, `memory_map_membership`, `memory_map_events` + RLS/grants | migrate up/down green; ownership PK rejects a 2nd membership row in test |
| 3 | Lifecycle spine: `mindmap_open` → `write_node` → `mindmap_get` → `mindmap_close` | a map opens (root slug minted), owns nodes, archives; exercised end-to-end in a test |
| 4 | **Live in-flight capture** (`890f7e47`) | nodes captured turn-by-turn into the named map, not post-hoc; documented pass/fail |
| 5 | Edges + `open` live-only similarity surface + `from_spec` dedup guard | candidates surfaced not merged; duplicate from_spec blocked |
| 6 | Graduation: propose → confirm → promote-with-`promoted-from` → archive | confirmed decisions become KB decisions referencing archived nodes; archived excluded from search, walkable via refs |
| 7 | Reviewer + ratification gradient + split-flag | counter hits 15 → sticky `review_due`; `mindmap_review` clears it; gradient + split-flag logged and surfaced |

## ADRs to record

- **ADR-0007** — `position`/`challenge` node types added to core; non-versioned
  (extends ADR-0005).
- **ADR-0008** — Mind-map lives in core, not a plugin; a map is a slug-addressed
  root memory + `memory_maps` state (records owner decisions 1 & 2).
