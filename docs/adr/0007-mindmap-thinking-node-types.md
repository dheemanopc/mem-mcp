# ADR 0007: Mind-map thinking-node types (position, challenge)

## Status

Accepted (2026-06-19)

## Context

The mind-map feature (docs/mindmap-v1-build-design.md) captures live reasoning
as first-class nodes: stances taken and objections raised. The existing
`memories.type` vocabulary was `note | decision | fact | snippet | question`
(see ADR-0005). Reasoning moves don't map cleanly onto those — a "position" is
not a `note`, and a "challenge" is not a `question`.

We must decide whether these are real node types in core, or a plugin concept
faked on top of `note` + a metadata discriminator.

Per the owner decision recorded in ADR-0008, the mind-map lives entirely in
core, and provenance *vocabulary* (the nouns) belongs in core regardless. The
two thinking-node types are vocabulary.

## Decision

Add `position` and `challenge` to the core `memories.type` CHECK constraint
(migration 0037).

Both are **working** thinking nodes: non-versioned, edit-in-place. They join
`note`/`snippet`/`question` in `NON_VERSIONED_TYPES`; only `decision` and `fact`
remain versioned (ADR-0005 is extended, not overturned). Their recency-decay
lambda matches `question` (0.05) — working memory that decays quickly.

A map-internal "decision" is NOT a new type: in-map decisions are ratified
`position` nodes, and become real `decision` memories only at graduation
(`mindmap_close`), where the slug + version machinery applies as usual.

## Consequences

### Positive
- Reasoning structure is queryable and typed without a metadata side-channel.
- Versioning/slug rules are untouched; the change is a pure constraint widening.
- A node that bundles several ideas can be split into linked typed nodes.

### Negative
- One more pair of values every `type` enumeration must track. Mitigation: a
  recency-table completeness test guards drift; the literals were updated in
  lockstep (write/search/list/update/search_chunks).

### Risks accepted
- Old flat memories never carry these types — fine; they remain unmapped nodes,
  consistent with the additive/non-breaking constraint.

## Alternatives considered

- **Fake as `note` + metadata.node_role**: Rejected. Pushes vocabulary into a
  metadata string the store can't constrain or index, and contradicts ADR-0008
  (vocabulary belongs in core). The owner explicitly chose core.
- **Make in-map decisions type=decision immediately**: Rejected. Forces slugs
  and versioning onto working thinking and blurs the graduation boundary that
  keeps the map separate from settled knowledge.
