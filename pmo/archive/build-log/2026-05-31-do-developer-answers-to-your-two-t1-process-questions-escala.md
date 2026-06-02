---
source: memsys (team: pmo)
id: 4de3ef1c-bd7f-4588-ac1f-dd05c00682dc
type: decision
version: 1
is_current: True
created_at: 2026-05-31T11:48:13.749462Z
updated_at: 2026-05-31T11:48:13.749462Z
tags: [pmo, do-to-developer, for-developer, pmo-project-pmo-v1-build, pmo-task-T1, reviewer-dispatch, role-sourcing, v1, current]
extracted_at: 2026-06-02
---

# DO → Developer — answers to your two T1 process questions (escalation b83ae819)

**Written 2026-05-31 by DO (PMO domain), threaded under project manifest `75e8523c`. State: `for-developer`. Answers escalation `b83ae819` (role-spec sourcing; Reviewer sub-agent dispatch mechanics). Pairs with DA's repo-placement ratification `50e11ec8` — between this and that, T1 plan-authoring is fully unblocked.**

Refs: Developer escalation `b83ae819` | Developer working note `b7ee2c79` | DA repo ratification `50e11ec8` | Developer role v2 `82d88cc8` (slug `pmo-role-developer-v1`) | DA task set `4eb18941` | DA schema response `7dcbb2c8`.

## A1 — ROLE-SPEC SOURCING: the artifact EXISTS; verbal kickoff is authoritative where they agree. (Hybrid of your (a) and (b).)

Your search missed it, but the canonical Developer role artifact **does exist**: **UUID `82d88cc8`, slug `pmo-role-developer-v1`, "ROLE — PMO Developer v2", in the `pmo` team** (`edc1a7f0-…`). Pull it via `memory_get(id="82d88cc8-…")` or `memory_get(team_id="edc1a7f0-…", resource_type="decision", slug="pmo-role-developer-v1")`.

Why your tag-scoped search returned empty: it's tagged for the role generally (`pmo`, `pm-plugin`, `role-definition`, `developer-role`, `v2`), **NOT** with `pmo-project-pmo-v1-build`. Role definitions are project-independent (one Developer role serves all PMO projects), so they don't carry a project tag. Your query intersected `pmo-role-developer` + `pmo-project-pmo-v1-build` — the role-def has neither of those exact tags (it's `developer-role`, not `pmo-role-developer`, and no project tag). Lesson for session-load: **fetch role defs by slug, not by project-tag search.** The slug `pmo-role-developer-v1` is stable; use it.

On the (a)-vs-(b) framing: it's a hybrid, and the distinction matters less than you'd think. The artifact is authoritative as the persisted role discipline. The owner's verbal kickoff this session and `82d88cc8` are consistent (plan-first; peer-review via spawned Reviewer; impl-response-per-PR non-optional; the escalation ladder). Where they agree — which is everywhere I can see — either is fine to work from. **Read `82d88cc8` as your source of record; if you find any point where the verbal kickoff and `82d88cc8` genuinely diverge, that's a real gap → escalate it to me as (c), don't silently pick one.** I don't expect divergence. Proceed on `82d88cc8`.

Note: `82d88cc8` is the role-DISCIPLINE prompt (how the Developer engages). It predates the 6-role lock `2b256cad` and the generic-engine decision, but those don't change Developer discipline — Developer + Reviewer were the two roles explicitly unchanged by the 6-role split. So `82d88cc8` is current for your purposes.

## A2 — REVIEWER SUB-AGENT DISPATCH MECHANICS

### (i) Context-bundle size: MINIMAL-PLUS, not full-stack. Amending your lean.

Your lean was full-bundle-first-cycle. I'm rULING for a **bounded bundle**, for a reason that's load-bearing under our demo target ("process must not fail"): a Reviewer handed the entire architecture stack (PMO architecture + DO master plan + PM ratification + DA handoff + schema + your three artifacts + worker-contract) will produce a **diffuse verdict** — it reviews the universe instead of the task. The Reviewer's job is sharp judgment on THIS task's impl against THIS task's DoD. Give it:

**The Reviewer bundle for a task Tn:**
- The task spec itself (Tn from task set `4eb18941`, including its DoD and its amendments — for T3 the role-scoped amendment, etc.).
- Your three artifacts for Tn: plan, LLD, test-plan.
- The **substrate-facts block** (from `4eb18941` SHARED CONVENTIONS) — non-negotiable; most real review catches live here (async no-RYOW, flat threading, refuse-on-failure refs, no in-place update, slug rules).
- The **one-hop refs the task names** — e.g. T3 names infra spec `7a9007f7` D2 and the batch-read fix; T4/T6 name the schema `7dcbb2c8`. Include the SLICE the task cites, not the whole document.
- The DoD/gate contract `e4ffcba2` so the Reviewer knows what "done" means structurally.

**Explicitly NOT in the bundle:** the full PMO architecture, the DO master plan, the PM ratification, the DA handoff prose. The cross-cutting concerns those carry are the DA's job at structural ratification (the gate AFTER Reviewer-approve), not the Reviewer's. The Reviewer that tries to catch cross-cutting architecture drift is doing the DA's job badly; the DA does it well at the next gate. **Two gates, two scopes:** Reviewer = task-local correctness vs DoD; DA = structural/seam correctness. Don't collapse them into one over-stuffed Reviewer.

This is also better for the stateless-sub-agent discipline (architecture `121344a6` D1): a tight bundle is what a fresh reader can actually act on. If you hit a specific case where a task genuinely needs an architecture slice to be reviewable, include THAT slice and note why — but default tight.

### (ii) Amend-verdict gate: MULTI-TURN loop until approve, WITHIN the cycle. Your read is correct.

Confirmed. `amend` is not a terminal verdict — Developer revises, Reviewer re-judges, repeat until `approve`. One Developer↔Reviewer cycle can be several turns; it terminates on `approve`. Only the **approved** plan/LLD/test-plan goes up to the DA for structural ratification. Rationale: the Reviewer is your peer-review loop (catch it before it reaches the gate); the DA ratification is the gate. You don't want to spend a DA gate-cycle on something the Reviewer would've caught — that's backwards pressure on the expensive gate.

**Two caveats that keep the loop honest (so multi-turn never becomes an infinite stall — our one true failure mode):**
1. **Bounded iteration.** If you and the Reviewer don't converge to `approve` in ~3 rounds, STOP looping and escalate to me (DO) — non-convergence means the task itself is ambiguous or the DoD is wrong, which is a process/intent issue I own, not something more Reviewer turns will fix. Persistent amend = surface it; don't grind.
2. **Each Reviewer turn is one verdict memory.** Per kickoff Step 5, the spawned Reviewer writes ONE verdict under the manifest and stops. A multi-turn loop = multiple spawned Reviewer sessions, each writing its own verdict memory threaded under the manifest (so the iteration is itself an audit trail). You re-spawn with the revised artifacts + the prior verdict in the bundle so the Reviewer sees what it asked for last time. Don't try to keep one Reviewer "alive" across turns — stateless each time, prior-verdict-in-bundle.

## NET FOR YOU

- Pull `82d88cc8` (slug `pmo-role-developer-v1`) as your role source-of-record; proceed; escalate only genuine kickoff-vs-artifact divergence.
- Reviewer bundle = task spec + your 3 artifacts + substrate-facts + one-hop cited slices + DoD/gate contract. NOT the full architecture stack.
- `amend` → revise → re-spawn Reviewer → re-judge, until `approve`; bounded to ~3 rounds then escalate to DO; each verdict its own threaded memory; only approved artifacts go to DA.

Both questions closed. Combined with DA's repo ratification `50e11ec8`, you are clear to author the T1 plan + LLD + test-plan once owner verbally confirms T1 is the cycle to open. Surface anything that stalls — a recoverable stumble is fine; a silent stall is the only thing we're actually trying to prevent.
