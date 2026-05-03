# ADR 0005: Versioning only on decision and fact types

## Status

Accepted (2026-04-19)

## Context

mem-mcp stores user memories as records with a `type` field: `decision`, `fact`, `note`, `snippet`, `question`. The spec requires version history so that users can audit changes and recover old values.

However, version history has a cost: extra rows, indexing, and query complexity. We must decide: do all types need history, or only some?

Analysis shows:
- **Decision** (e.g., "avoid carbs for energy"), **Fact** (e.g., "Alice's birthday is March 5"): valuable in their HISTORY. Users want to see "what was I thinking before?" and "when did I learn this?"
- **Note** (e.g., "meeting notes"), **Snippet** (e.g., "Python snippet"), **Question** (e.g., "how does X work?"): are primarily working memory. Users edit them in place and don't care about history.

Versioning only decisions and facts keeps the schema lean while preserving audit trails for the highest-value records.

## Decision

Only `decision` and `fact` type records are versioned (immutable history, content_version increments on each edit).

For `note`, `snippet`, and `question`: mutations are in-place; no version history is recorded.

## Consequences

### Positive
- Reduced storage and indexing burden for high-volume working memory (notes)
- Simpler query logic for mutable records (no version filtering)
- Clear semantic split: audit trail for high-value records, edit-in-place for ephemeral ones
- Users can still export all memories at any time for personal backup

### Negative
- If a user accidentally edits/deletes a note, there's no recovery path (other than full db backups)
- Inconsistent behavior: some types have history, others don't. Mitigation: document clearly in API spec.

### Risks accepted
- Users may lose working memory (notes) on accidents. Mitigation: soft-delete flag + async cleanup (TTL 30d); users can recover within 30 days.

## Alternatives considered

- **Version everything**: Rejected. Bloats the schema and query logic. For closed beta, the cost-benefit is poor.
- **No versioning at all**: Rejected. Violates spec §5.2 (audit trail required for decisions and facts).
