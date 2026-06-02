---
source: memsys (team: pmo)
id: ea4aff7e-4dcb-4de4-9947-cadf77cede0c
type: decision
version: 1
is_current: True
created_at: 2026-06-01T05:15:51.548825Z
updated_at: 2026-06-01T05:15:51.548825Z
tags: [pmo, v2-backlog, feature-request, project-setup-generator, role-prompt-skeleton, for-pm, for-da, pmo-project-pmo-v1-build, current]
extracted_at: 2026-06-02
---

# PMO v2 FEATURE — Project-setup generator: intent-driven scaffolding of role-prompts + matrix + workflow from a setup dialogue

**Written 2026-06-01 by DO (PMO domain), threaded under manifest `75e8523c`. State: `v2-backlog` / `for-pm` / `for-da`. Owner-requested v2 feature, explicitly deferred from v1. Captured now while the design intent is fresh. This is the FIRST PMO v2-backlog item; future v2 planning threads under or references this.**

Refs: PMO architecture (locked) `121344a6` | PM 6-role lock `2b256cad` | config/matrix schema `7dcbb2c8` | spine-complete handoff `46f91aa6` | DO master plan `141a9f5e`.

## THE OWNER'S REQUEST (verbatim intent)

"I want these prompts to be stored as skeleton. While setting up a project, we should also have a setup prompt that will ask what kind of project the user wants, how much control, and based on that setup the prompts for that project. Even the workflow etc. If that's not doable in v1, ok — track it for v2."

## WHAT THIS IS, ARCHITECTURALLY

v1 makes **role = config** (the engine is generic; a role is a matrix slice + a config record + a role-prompt, all hand-authored by DO). This v2 feature adds a layer ABOVE that: **config-as-generated-output.** A setup dialogue elicits project intent and EMITS the artifacts v1's engine consumes — role-prompt skeletons, the permission matrix, the six (or N) role-configs, and the workflow/gate-cycle shape — tailored to the project.

So the dependency is strict and is WHY this is v2, not v1: you cannot sensibly build a generator for an artifact whose consumer (the engine + hand-authored config) is not yet proven live. v1 must first demonstrate the engine runs on hand-authored matrix + configs (DO's current critical-path content). Once that round-trips, the generator is the natural next layer. Building the generator first would be backwards.

## THE THREE ELICITATION AXES (the "setup prompt")

1. **What kind of project** → selects a STARTING TEMPLATE. The 6-role PMO model is one template (software-build with project/milestone/epic/story/task tiers). Other project shapes may want fewer tiers or different role names (e.g. a research project, a content pipeline, a solo build). The setup dialogue picks/derives the template, then tailors it. The locked 6-role model (`2b256cad`) becomes the DEFAULT template, not the only one.

2. **How much control** → sets the GATE-CYCLE STRICTNESS DIAL. This is the genuinely architectural axis. The framework already has the vocabulary for it: full-strict = every artifact through proposal→ratify→test-plan→ratify→implement→impl-response→ratify with Reviewer + DA gates (what v1 PMO does); lighter = collapse gates (e.g. skip the separate test-plan gate, or let Developer self-review for low-risk tasks); lightest = single-owner with advisory-only roles. "How much control" maps to WHICH gates are mandatory vs optional vs off, and to how many distinct roles exist. This dial should be a first-class generated parameter, not a fork in code.

3. **Workflow** → derives the WORK-ITEM HIERARCHY + STATE VOCABULARY + ROUTING shape for the project. v1 fixed project/milestone/epic/story/task + draft/ready/claimed/in-progress/done. v2 generates these per project type (a solo content project might be just project/task with draft/published).

## WHAT GETS GENERATED (the outputs)

From the dialogue, the generator emits — into memsys, against the v1 engine's contracts:
- **Role-prompt skeletons** — the per-role discipline prompts (like the v1 role specs `547b32ad`/`9afdef9d`/`82d88cc8`/`3c6de6e0`), parameterized by the chosen roles + control level. THIS is the "store prompts as skeleton" ask: a library of role-prompt templates with fill-in slots (project name, tier names, gate strictness, escalation ladder), instantiated per project.
- **The permission matrix + role-configs** (schema `7dcbb2c8`) — generated, not hand-authored. The generator IS the thing that would have produced what DO authors by hand in v1.
- **The workflow definition** — tiers, state vocabulary, gate-cycle config (which gates are on), routing/escalation ladder.
- **The project manifest** — seeded with the above, ready for roles to register against.

## WHY IT'S COHERENT WITH THE LOCKED ARCHITECTURE

This does NOT contradict any v1 lock. It sits cleanly on top:
- Role-as-config (`121344a6`) is the enabler — because a role is already pure data, GENERATING that data is a well-posed problem.
- The generic engine (already built + verified, T6) consumes generated config identically to hand-authored config; no engine change needed.
- The matrix schema (`7dcbb2c8`) is the generation TARGET; the generator must emit valid instances (config-verbs ⊆ matrix, named default-list queries that exist in the resolver registry, etc.). The schema's load-time invariants become the generator's correctness contract — a nice property (the engine validates what the generator emits).

## OPEN DESIGN QUESTIONS (for v2 planning, not now)

- Is the generator itself a PMO role (a "Setup" / bootstrap role with its own prompt), or a standalone tool/skill invoked once at project creation? (Leaning: a setup skill/dialogue, since it runs once before roles exist.)
- Skeleton storage: where do role-prompt templates live — memsys memories (versioned, team-scoped), or files in the plugin repo? (memsys gives versioning + the same substrate; files give git review. Possibly both: canonical in repo, instantiated copies in memsys.)
- The control-dial → gate-config mapping needs a concrete enumeration (which gate combinations are valid; can't have impl-response-ratify without implement).
- Validation: the generator must emit schema-valid matrix+configs; reuse the v1 engine's `validate_invariants` as the generator's output check.
- Multi-template management: how templates are added/versioned as new project shapes are supported.

## SCOPE FENCE

NOT v1. v1's remaining critical path is unchanged: DO authors the matrix + six configs + Area A + D1 sealed note BY HAND, the engine runs on them, the operator runbook (`23259bee`) upgrades assurance. Only after the hand-authored path round-trips live does the generator become buildable. This memo exists so the idea is not lost; it is tracked, not started.
