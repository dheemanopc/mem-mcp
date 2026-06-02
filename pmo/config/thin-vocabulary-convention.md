---
source: memsys (team: pmo)
id: 940cfbae-e4bf-482f-8380-f39bb859d74c
type: decision
version: 1
is_current: True
created_at: 2026-06-01T05:25:52.986898Z
updated_at: 2026-06-01T05:25:52.986898Z
tags: [pmo, area-a, vocabulary-convention, do-content, for-da, for-developer, for-pm, pmo-project-pmo-v1-build, v1, current]
extracted_at: 2026-06-02
---

# PMO v1 — Area A: Thin Vocabulary Convention

**Authoritative naming + convention surface for the PMO framework. DO-owned content (the Area A deliverable from master plan `141a9f5e`). THIN by design: names and meanings, not schema. Every role, prompt, tag, and reference refers to this so they stay consistent. The matrix/configs (`pmo-permission-matrix-v1`), the plugin mechanisms (T1–T7), the routing contract (Area C), and the tmux substrate (Area E) all point back here for vocabulary; none of that lives here.**

Sources reconciled: PM 6-role lock `2b256cad` · config/matrix schema `7dcbb2c8` · DO master plan `141a9f5e` · PM working-memory ruling `ba6d113a` · DO durability caveat `b29f1a10` · T7 registration trio `c9095015` · infra spec `7a9007f7`. Manifest `75e8523c`; PMO team `edc1a7f0-9f5a-4dd7-b045-ccd9a418b2d9`.

## 1. WORK-ITEM LEVEL NAMES (hierarchy spine)

Five tiers, parent→child; child→parent via a `references` edge of kind `derived-from` (§3), traversed with `refs_in`/`refs_out`.

- `project` — the whole endeavor; one manifest root per project. Owned: PM (intent) + PA (structure).
- `milestone` — a shippable/demoable increment within the project. PM + PA.
- `epic` — a coherent body of work within a milestone. DM (intent) + DA (structure).
- `story` — owner-meaningful unit within an epic; carries state (§4). DM/DA define; Developer claims.
- `task` — smallest executable unit; one gate cycle. Developer.

Taxonomy is JUST-IN-TIME, per-tier: PM authors project+milestones; DM/DA author epics on engagement; Developer authors stories/tasks at execution. This document names levels; it does not enumerate instances.

## 2. SIX ROLES + OWNERSHIP MAP (intent vs structure at every tier)

One principle recursively: at every tier an intent-owner pairs with a structure-owner (`2b256cad`).

- `pm` — Project Manager — project tier — INTENT (what/why, project success).
- `pa` — Project Architect — project tier — STRUCTURE (how milestones compose; cross-milestone).
- `dm` — Domain/Milestone Manager — milestone/epic — INTENT (which epics, order, milestone success).
- `da` — Domain/Milestone Architect — milestone/epic — STRUCTURE (how stories compose; cross-epic seams).
- `developer` — task — MECHANISM (implements tasks; surfaces blockers).
- `reviewer` — task — JUDGMENT (stateless task-local verdict vs DoD).

PA/DM/DA long-form names are working names (concept+tier locked; names may be refined by owner/PA). The `code` (pm/pa/dm/da/developer/reviewer) is the STABLE identifier used in tags (`pmo-role-<code>`), in the matrix/configs `roles[]`, and in routing. Codes are canonical; never key off the long-form name.

Escalation ladder by ownership: mechanism → Developer (escalates structural→DA / intent-process→DO/PM); structural → DA; intent/scope → DM → PM. Surface, never silently absorb.

## 3. REFERENCE-KIND REGISTRY (edge vocabulary)

`references.reference_kind` values the framework writes (unknown kinds are substrate-allowed but off-convention; add here first):

- `derived-from` — child work-item → parent (the spine: task→story→epic→milestone→project). `pinned`.
- `supersedes` — formal artifact replaces a prior version; via `memory_supersede`, not a manual edge. `pinned`.
- `responds-to` — a cross-role memo answers/escalates a prior memo. `pinned`.
- `ratifies` — a gate verdict (Reviewer/DA/PM) cites the artifact it ratifies. `pinned`.
- `blocks` / `blocked-by` — a work-item/gap blocks another. `current` (tracks live state).

Default `refs_version` = `pinned` for all except `blocks`/`blocked-by` (`current`). Spine edges are always `pinned`.

## 4. STORY-STATE VOCABULARY (pure convention, NO engine enforcement v1)

State is a tag: `pmo-state-<state>`. Per `7dcbb2c8` F2 + master-plan delta 7: the engine READS state-tags but does NOT validate transitions in v1; concurrency-safe claiming deferred to v2 (single operator v1). States: `draft` (authored, not ready) → `ready` (available to claim) → `claimed` (a role took ownership; registration §6 binds who) → `in-progress` (actively worked) → `done` (complete at owning tier's bar). Flow is advisory, not enforced; transitions owned by the work-item's tier-role.

## 5. WORKING-MEMORY DISCIPLINE (when to write to memsys)

Authoritative complement to infra spec `7a9007f7` D3. Mental model: within-role across-time state lives in the SESSION (retention covers it); cross-boundary state lives in MEMSYS (PM ruling `ba6d113a`).

WRITE when: content must be visible to ANOTHER role (cross-role hand-off, escalation, nudge, gate verdict); it's a `pmo-user-response` (owner's words verbatim — always); it's an audit-grade decision or a stabilized hand-off another session/role resumes from.

DON'T WRITE when: self-reflection, summary of own reasoning, status/ack note, stream-of-thought, or a draft only you use. When in doubt: don't write.

DURABILITY CAVEAT (`b29f1a10`): session retention is convenience, NOT durable storage. A session lost (crash, context overflow, reset) before bridging to memsys loses retained-but-unwritten state. Rule: RETAIN FOR SELF, PERSIST FOR OTHERS — AND persist anything a post-loss resume will need. Narrow boundary condition, not a re-expansion of self-note writing.

(v1.5, tracked not built: optional `purpose` enum on the write helper — `cross_role_handoff|user_response|audit_decision|stabilized_handoff` — refusing self-continuity writes. Prompt discipline suffices v1.)

## 6. REGISTRATION TAG SCHEMA (identity ↔ role ↔ work-item)

Per shipped T7 (`c9095015`). A registration is a working leaf under the work-item; tags are the discovery surface; body = `{session_id, cursor, registered_at}`. Re-registration writes FRESH (no in-place); latest by `created_at` recency within the tag-filtered set.

Registration tag set (always): `pmo`, `pmo-working`, `pmo-role-<role>`, `pmo-project-<project-slug>`, `pmo-registration`, `pmo-identity-<identity-uuid>`; OPTIONAL `pmo-state-<state>` (§4).

Self-discovery query tags (the "what's on my plate" lookup) EXCLUDE `pmo-project-<slug>` so the query is cross-project: `pmo-registration`, `pmo-role-<role>`, `pmo-identity-<identity>`, optional `pmo-state-<state>`.

## 7. STANDARD WORKING-MEMORY TAG SET (general leaf)

Every non-registration working memory under the manifest carries five base tags (T2 path): `pmo`, `pmo-working`, `pmo-role-<role>`, `pmo-project-<project-slug>`, and one working-type tag (e.g. `pmo-escalation-to-do`, `pmo-user-response`, `pmo-working-note`). Always `indexable=false` (semantic-non-polluting, reachable by thread-get + tag-list, SF-13). Threaded under the manifest root via `parent_id`.

## 8. PROJECT-SLUG CONVENTION

`pmo-project-<project-slug>`, `<project-slug>` = stable kebab-case project id (this project `pmo-v1-build` → `pmo-project-pmo-v1-build`). Fixed at project creation; used in every working/registration tag and in the manifest.

## SCOPE FENCE

THIN — names and conventions only. NOT here: permission matrix/configs (`pmo-permission-matrix-v1` against `7dcbb2c8`); plugin mechanisms (T1–T7); routing/escalation contract + sealed D1 fallback (Area C, separate DO deliverable → DA composition-check); tmux substrate (Area E). Those refer back here for vocabulary.
