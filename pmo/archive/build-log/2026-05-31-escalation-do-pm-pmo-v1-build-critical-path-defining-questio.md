---
source: memsys (team: pmo)
id: 991886c2-1fbf-4dde-95f8-0a35e6b58a98
type: decision
version: 1
is_current: True
created_at: 2026-05-31T08:44:11.219606Z
updated_at: 2026-05-31T08:44:11.219606Z
tags: [pmo, escalation, do-to-pm, pmo-project-pmo-v1-build, pmo-escalation-to-pm, v1, current]
extracted_at: 2026-06-02
---

# ESCALATION DO→PM — PMO v1 build: critical-path-defining questions before sequencing

**Written 2026-05-31 by DO (PMO domain), addressed to PM. State: awaiting PM response. Closes the open-ambiguities loop from the handoff memo (3823d5bb).**

Refs: handoff `3823d5bb` | architecture `121344a6` | orchestration v1.5 `0d93f919` | manifest root `75e8523c`.

## CONTEXT FOR PM

DO has loaded full context and engaged the owner in dialogue. The owner resolved several handoff open-ambiguities and then redirected the remaining intent-level questions to the PM (correct per the escalation ladder — these are WHAT/WHY questions, PM's authority, not DO mechanism calls).

### Already resolved by owner (recording for PM's view; not re-asking):
- **Demo audience:** moot. Demo = LIVE feature-building by users on memsys-as-substrate. Not a discipline-deck.
- **tmux:** present Monday; manual `claude --resume` routing is the demo's routing; orchestration auto-routing stays v1.5.
- **"PMO via PMO":** it's a SIMULATION. Infra plugin doesn't exist yet, so the roles are simulated (owner in tmux panes, role prompts + raw memsys tools) while the actual build deliverable is the PMO infra plugin itself.
- **Worker-contract refinement timing:** process-hardening, not a build gate. Infra-as-plugin is priority; fold refinement in later.

## THE QUESTIONS FOR PM (intent-level; they define the critical path)

### Q1 — DEMO TARGET (the load-bearing one)
Is Monday's live demo driving the actual `pmo_*` role tools — meaning **the infra plugin must be built and working end-to-end by Monday** — OR is the demo **simulating the roles with raw memsys tools** (`memory_write`/`memory_get`/etc.), with the PMO plugin being the thing built *toward* but not yet demoed *through*?

This single answer forks the entire sequence:
- If demo-through-the-plugin → critical path is "ship infra plugin end-to-end" (aggressive for a Monday).
- If demo-by-simulation → critical path is "memsys primitives rock-solid + role prompts clean enough to simulate by hand"; the plugin build proceeds behind the demo, not gating it.

The owner's "it's a simulation" comment leans toward the latter, but DO wants PM to state the demo target explicitly because it changes what ships first.

### Q2 — BATCH-READ BUG PRIORITY
`memory_get_batch` slug-tuple path is BROKEN on prod right now (`invalid input syntax for type uuid: ""` — empty `id` default cast to UUID before the slug branch). Every role-tool session-start load pattern depends on it. Impl-response `95aab258` is awaiting-verification and the integration tests for this exact path were deferred.

If the demo touches session-start load live (likely under either Q1 answer, since even simulation uses batch reads), this is **the first fix**, ahead of any PMO plugin work. DO recommends opening it as a tracked memsys-core bug now and making its regression test (slug-tuple batch resolution) the gating fix. **PM: confirm this jumps the queue, or tell DO the demo path avoids it.**

### Q3 — ESCALATION-SURFACING SEAM (one genuine spec gap)
The Architect, Developer, and Reviewer specs each defer "how does `escalation_to_pm`/`escalation_to_architect` surface to the target role?" to the worker, suggesting a default of tag + manifest-thread + target's `list(what="pending_intake")`. But the PM tool spec's `list` enum has NO `pending_intake` — it has `pending_ratifications` and `assigned_intake`. Three downstream specs reference a PM list-key that doesn't exist in the PM spec. Real seam mismatch from overnight authoring.

DO recommends locking the escalation-surfacing contract as ONE decision before the role-tool wave (three workers proposing it independently will diverge). It's a shared cross-role seam — architectural-adjacent — so DO surfaces rather than silently reconciling. **PM: lock-first, or let-workers-propose-and-reconcile-at-integration?**

## DO'S POSTURE

DO is NOT authoring the sequencing plan until Q1 is answered (it forks the plan). Q2 and Q3 DO can act on with PM's yes/no. Once Q1 lands, DO will draft the full first→next→granular sequence and bring it back to the owner/PM for ratification before any worker dispatch.
