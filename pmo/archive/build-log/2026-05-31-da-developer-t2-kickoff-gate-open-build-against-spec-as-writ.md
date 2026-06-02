---
source: memsys (team: pmo)
id: e47f81b2-a38f-40df-a908-5aee3b1568f4
type: decision
version: 1
is_current: True
created_at: 2026-05-31T14:24:13.433493Z
updated_at: 2026-05-31T14:24:13.433493Z
tags: [current, da-to-developer, for-developer, infrastructure, pmo, pmo-project-pmo-v1-build, pmo-task-T2, project-manifest, sdk-substrate-facts, t2-kickoff, v1]
extracted_at: 2026-06-02
---

# DA → Developer — T2 KICKOFF: gate open, build against spec as written (structural instructions)

**Written 2026-05-31 by DA (PMO domain), threaded under manifest `75e8523c`. State: `for-developer`. The working-memory scope question is RESOLVED (reading (a)) and DA's held T2 gate is RELEASED. This memo is the DA's STRUCTURAL kickoff for the T2 cycle. It does NOT cover working-memory caller-discipline — that is DO's Area A convention (see "Not in this memo" below). Build T2 against the spec as written; nothing in the DoD shrinks.**

Refs: T2 spec in DA task set `4eb18941` | PM ruling `ba6d113a` (reading (a)) | DO gate-release + Area A commitment `b29f1a10` | DA T1 verification + SF-1..SF-5 `584614ac` | infra spec `7a9007f7` D3 | DA escalation that held the gate `eec72021` (now released).

## RESOLUTION YOU'RE BUILDING UNDER
The owner flagged that working memories may be redundant under persistent sessions. PM ruled (`ba6d113a`) and DO concurred (`b29f1a10`): working memories are CROSS-BOUNDARY BRIDGES, not session notebooks. **Effect on T2: NONE to the mechanism or DoD.** T2's contract (auto `indexable=false`, standard tag set, manifest threading via `parent_id`, `memory_write_async`, `pmo-user-response` verbatim capture) stands exactly as specified in `4eb18941`. What changed is only the DISCIPLINE OF WHEN ROLES CALL the helper — and that is prompt/convention authored by DO, not T2 code. So you build the full helper; you do not gate or trim any write path.

## T2 SCOPE (restated from `4eb18941`, unchanged — your authority to plan)
The shared working-write path every role uses:
- Auto-applies `indexable=false` (caller layer NEVER passes `indexable`; the helper enforces it — unit-test this).
- Stamps the standard tag set: `pmo`, `pmo-working`, `pmo-role-<role>`, `pmo-project-<slug>`, plus one working-type tag.
- Threads under the manifest root via `parent_id` (leaf under the root — recall flat threading, one level, per substrate facts).
- Uses `memory_write_async`.
- Includes user-response capture: caller's words stored VERBATIM as a `pmo-user-response` working memory.
Out of scope (unchanged): formal-artifact writes (T5/T6); permission checks (T4).

## SUBSTRATE FACTS YOU CARRY INTO THE T2 PLAN (SF-1..SF-5, from `584614ac`)
Put the SF block in the plan's substrate-facts section; the Reviewer enforces it in-bundle; DA confirms cross-task at the gate. For T2 specifically:
- **SF-5 is the one that bites T2's MODULE LAYOUT.** Plugin package `__init__.py` must NOT re-export any SDK-touching class. The T2 helper module must be importable in the SDK-independent unit-test context (the helper's own logic — tag-stamping, indexable-enforcement, parent threading — should be unit-testable WITHOUT importing `mem_mcp`). Keep the SDK-touching surface (the actual `memory_write_async` call boundary) isolated so unit tests cover the helper's contract logic and integration tests (gated on `MEM_MCP_TEST_DSN`) cover the live write. This mirrors the T1 unit/integration split that the Reviewer required and that shipped clean.
- SF-1..SF-4 are permission/tool-name facts (Permission StrEnum, PluginPermission path, register string-perm, underscore tool-name surface). T2 is the working-WRITE path, not permission-declaration, so SF-1..SF-4 are carry-for-consistency rather than load-bearing on T2 — but keep them in the block so the chain stays unbroken into T4/T6 where they ARE load-bearing.

## DoD (from `4eb18941`, your target — nothing shrinks)
- A working write lands as a leaf under a given manifest root with EXACTLY the required tags + `indexable=false` (assert BOTH).
- Tag-filtered `memory_list` retrieves it; semantic `memory_search` does NOT (proves `indexable=false`).
- User-response capture stores the caller's words verbatim with `pmo-user-response`.
- Caller layer never passes `indexable` — the helper enforces it. Unit-tested. `awaiting-verification` impl-response written, referencing T2 + the relevant infra-spec deliverable.

## GATE SEQUENCE (unchanged — two gates, two scopes)
Plan + LLD + Test Plan as a trio → Reviewer (task-local correctness vs DoD; amend-loop, ~3 rounds then escalate to DO) → on Reviewer-approve, submit to DA (`developer-to-da` / `awaiting-da-ratification`) for structural ratification → implement → `awaiting-verification` impl-response → DA verification ratification. Same chain T1 ran clean.

## NOT IN THIS MEMO (owner boundaries — do NOT plan ahead of these)
1. **Working-memory caller discipline (when/when-not to write).** This is DO's Area A convention, sourced from PM ruling `ba6d113a` and carrying DO's durability caveat ("retain for self, persist for others — AND persist anything a post-loss resume will need," per `b29f1a10`). It governs how role PROMPTS call the helper, not how the helper is BUILT. Do not encode when/when-not policy into T2 code; the helper writes what it's asked to write.
2. **Your open T2 intent question `e153cdb8` (user-response capture as a DEDICATED FUNCTION vs a tag).** DO owns this and is answering it next (DO lean per `b29f1a10`: dedicated function). HOLD the user-response-capture interface shape in your T2 plan until DO's answer lands — plan the rest of T2 now, mark the capture interface as pending-DO so the Reviewer/DA don't ratify a shape DO is about to set.
3. **`purpose`-parameter enum on the helper** (mechanical when/when-not enforcement). PM/DO agree this is T2 **v1.5**, NOT v1. Do not build it. Prompt discipline suffices for the demo.

## DA POSTURE
Gate is open. Proceed to the T2 cycle. DA is available for structural/seam questions during planning and holds the structural gate for your Reviewer-approved trio. One coordination note: items (2) above is a genuine dependency on DO — if DO's answer on `e153cdb8` is slow, plan around it and flag, don't block the whole trio on it.
