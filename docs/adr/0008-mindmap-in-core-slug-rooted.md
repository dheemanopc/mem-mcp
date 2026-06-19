# ADR 0008: Mind-map lives in core; a map is a slug-rooted memory

## Status

Accepted (2026-06-19)

## Context

The mind-map functional model (`memsys-mindmap-spec-v0.1`) was first designed
with a core/plugin seam: node vocabulary in core, the lifecycle protocol in a
new `mindmap` plugin (mirroring the `reminders` plugin). At build-design review
the owner overrode this on two points that shape the entire implementation.

## Decision

**1. Everything lives in core.** There is no mind-map plugin. The map tables,
lifecycle tools (`mindmap_open/write_node/link/get/review/close`), the reviewer
write-counter, and the swappable driver/reviewer prompts all ship inside
`src/mem_mcp`. The prompts remain swappable (asset files under `skills/`) so
judgment can be re-tuned without touching core code.

**2. A map's identity is a memsys key.** A map is a root memory plus the nodes
that hang off it; the root memory is minted a slug (`slugs.resource_type='map'`,
migration 0037) and that slug *is* the map key the driver passes to every tool.
There is no opaque map-id registry. The lifecycle state that is not node content
(live/archived, write-counter, seed origin, graduated index) lives in
`memory_maps`, keyed by `root_memory_id`.

**3. One map per conversation is convention, not design.** Working a single map
at a time is driver discipline (the mindmap-driver skill), not a schema-enforced
lock. Map tools take the map key explicitly; there is no enforced active-map and
no conversation-id plumbing.

Exclusive *node* ownership remains a structural guarantee, separately: a node
belongs to at most one map, enforced by `memory_map_membership`'s PRIMARY KEY on
`memory_id`.

## Consequences

### Positive
- No plugin boundary to cross: map nodes are ordinary core memories created via
  the existing `MemoryWriteTool` path, inheriting embeddings/dedup/audit/RLS.
- The map key is human-readable and reuses the slug machinery (minting, collision
  retry, reserved-word checks) with zero new identifier scheme.
- Edges reuse `memory_references` (open-text `reference_kind`) — no new edge table.
- Dropping the active-map lock sidesteps the unresolved conversation-id question.

### Negative
- Core grows three tables and six tools that a plugin seam would have isolated.
  Accepted: the owner judged a judgment-bug in a plugin no cheaper to fix than in
  core, and the plugin boundary added more cost than it saved here.
- Map creation spans two transactions (memory write, then map rows). A failure in
  between can orphan a root memory. Accepted for v1; the unsluggable-title guard
  removes the most likely failure before the root is written.

### Risks accepted
- Slug vocabulary now includes `map`; user-facing slug-lookup tools still expose
  only `decision`/`fact`, so map keys are resolved internally by the map tools.

## Alternatives considered

- **Mind-map as a plugin (original seam)**: Rejected by owner. Storage is firm
  and judgment is rippable either way; the plugin boundary did not pay for itself.
- **Opaque UUID map ids**: Rejected. The owner wanted the map addressed by a
  memsys key so maps are first-class, human-addressable memories.
