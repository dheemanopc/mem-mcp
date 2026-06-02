---
source: memsys (team: pmo)
id: 03e83396-500e-4131-81a2-cfbb80a2ba4c
type: decision
version: 1
is_current: True
created_at: 2026-06-01T12:47:30.843204Z
updated_at: 2026-06-01T12:47:30.843204Z
tags: [pmo, prompt-patch, directive-emission, atpmo-marker, role-prompts, for-pm, for-da, for-developer, pmo-project-pmo-v1-build, v1, current]
extracted_at: 2026-06-02
---

# PMO prompt-patch — DIRECTIVE EMISSION block (v2 `@pmo` marker) for all six role prompts

**Authoritative replacement for the DIRECTIVE EMISSION section in all six v3 role prompts (PM `127793ef`, PA `897c696d`, DM `99c6c6cd`, DA `4e29970b`, Developer `da1c4c82`, Reviewer `6fff0238`). Those prompts were written against directive grammar v1 (`@PMO NEXT resume=... role=... note=...`); the grammar is now v2 (`ab0e7126`, `@pmo /resume <id>` matching the installed Stop hook). This patch replaces the emission block in each; where a prompt's text and this patch differ, THIS PATCH WINS. Threaded under manifest `75e8523c`.**

Refs: directive grammar v2 `ab0e7126` | routing config v2 `714c71a6` (auto-Enter) | the six role prompts (above) | installed hook `pmo-stop-hook.sh`.

## THE REPLACEMENT EMISSION BLOCK (use in place of the DIRECTIVE EMISSION section in every role prompt)

```
DIRECTIVE EMISSION (routing — grammar v2)

When a hand-off to another role should happen, emit as the LAST line of your response:

    @pmo /resume <session-id>

- <session-id> is the TARGET role's Claude Code session id. Resolve it from the
  session-registry you loaded on resume (role -> session-id). You emit the clear,
  complete command; the Stop hook isolates "/resume <id>" and switches the single
  window to that session (it auto-submits in v1).
- Emit NO @pmo line if you are still in dialogue or awaiting the human. That is normal.
- Emit exactly ONE @pmo line, and ONLY for a /resume hand-off.

For NON-routing intents, do NOT emit an @pmo line — state them in prose:
- ESCALATION: write the escalation as a working memory (tags pmo-escalation,
  pmo-escalation-to-<role>, pmo-escalation-open) and tell the human in prose who you
  are escalating to and the memory id. The human routes.
- SPAWN (a role/domain doesn't exist yet): say so in prose; the human runs `pmo spawn`.
- DONE / REVIEW_REQUEST: state in prose; terminal or awaiting the human.

The directive is a POINTER; the substance (decisions, escalation content) lives in
memsys memories the next role reads on its own resume. Never assume the hand-off
fired beyond emitting the line.
```

## PER-ROLE TARGET NOTES (the typical /resume target for each role's hand-off)

- **PM**: after project_brief + milestones land → `@pmo /resume <PA session-id>` (or, if no PA exists, say in prose "spawn a PA").
- **PA**: after project structure set → `@pmo /resume <DM session-id>` (or prose "spawn a domain").
- **DM**: after epic/intent defined → `@pmo /resume <DA session-id>`.
- **DA**: after story + story_dod ready → `@pmo /resume <Developer session-id>`; after structural ratify → `@pmo /resume <DM session-id>`.
- **Developer**: trio ready → `@pmo /resume <Reviewer session-id>`; impl-response posted → `@pmo /resume <DA session-id>`.
- **Reviewer**: approve → `@pmo /resume <DA session-id>`; amend → `@pmo /resume <Developer session-id>`.

These are typical, not exhaustive; the agent picks the correct next role from context and resolves its id from the registry.

## NOTE
This patch is applied IN PLACE of the v1 emission block; the rest of each v3 prompt (persona, on-resume, contract, working-memory discipline, escalation ladder) is unchanged. When the prompts are next materially revised, fold this block in and retire this patch. For now it is the authoritative emission contract the six prompts inherit.
