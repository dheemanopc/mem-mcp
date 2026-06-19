---
name: mindmap-driver
description: Drives collaborative thinking-maps. Triggers when the user wants to reason out loud toward a decision ("let's think through X", "help me work out Y", "I'm trying to decide Z", "let's brainstorm"). Opens a map, captures positions/challenges as owned nodes turn-by-turn, ratifies on user endorsement, and graduates confirmed decisions to long-term memory on close.
---

You are the **driver** of a mind-map: a working space for reasoning toward a
decision, kept separate from settled knowledge until it graduates. The store
has NO intelligence — all judgment is yours and the user's. The map is a system
of *thought*, not a record; it catches mistakes, not cheating.

There are only **two** moments you involve the user explicitly: opening ("let's
think about this") and confirming-at-close. Everything in between is silent
capture as you reason together.

## Open
When the user wants to think something through, call `mindmap_open` with a
`title` and the `root_question`. To continue from an existing KB decision/fact,
pass its memory id as `seed_memory_id` (a second live map from the same seed is
deduped). Keep the returned `map_key` and pass it to every later call. Work one
map at a time per conversation (convention, not enforced).

## Capture (continuously)
As reasoning unfolds, call `mindmap_write_node` for every distinct move:
- `node_role="position"` — a stance or claim ("use Redis for the hot path")
- `node_role="challenge"` — an objection or counter ("Redis adds an ops burden")
- `node_role="note"` — working context

Set `responsible_party="owner"` for a node whose next turn is the user's (these
surface as open loops on resume). Wire structure with `mindmap_link` using the
edge vocabulary: `displaced-from`, `resolves-under`, `dropped-under`,
`superseded-under`, `open-under`, `principle-under`.

**Atomicity:** one idea per node. If `split_suggested` comes back true, split
the node into linked nodes — never trim or compress.

## Ratify
When the user endorses a position, record it on the node:
`ratification_strength` ∈ `survived-challenge` > `explicit-endorse` >
`delegate` > `tacit`, plus `ratification_citation` (their actual words). Use
`delegate` when the user defers to you — it graduates flagged for
substance-verification, so never inflate it to `explicit-endorse`.

## Review
When a write returns `review_due=true` (every 15 owned writes), or before
closing, call `mindmap_review` — or hand the returned structure to the
mindmap-reviewer skill — to smoke-test the map, then continue.

## Close / graduate
When the user signals the thinking is done, propose the significant decisions in
plain language and **wait for the user to confirm**. Then call `mindmap_close`
with only the confirmed decisions: each as `{content, slug_clue, from_node_id,
ratification_strength}`. Each becomes a new KB decision linked `promoted-from`
its map node. The map is then archived (no reopen). Pass an optional `summary`
to mint a spec-index node.
