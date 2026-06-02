---
source: memsys (team: pmo)
id: 3c5900e5-b7c3-4587-a0d0-cfe6fc03afc3
type: decision
version: 3
is_current: True
created_at: 2026-06-01T09:13:43.531770Z
updated_at: 2026-06-01T09:13:43.531770Z
tags: [pmo, area-e, tmux-substrate, one-window, orchestrator-driven, guidance-routing, do-content, demo-track, for-pm, for-da, for-developer, pmo-project-pmo-v1-build, v1, current]
extracted_at: 2026-06-02
---

# Area E — substrate PLAN v3 (one-window, orchestrator-spawned, guidance-driven)

**DO-authored. SUPERSEDES Area E v2 `27b8337a`. This v3 captures the FULL corrected model the owner walked through: a single-window, human-followed, AGENT-DRIVEN flow where the orchestrator spawns roles on demand, agents emit machine-tradable guidance in chat, and a hook (later) reads that guidance and pre-fills the next `/resume` via tmux for the human to Enter. Routing governed by the per-directive-type config `08b19eee`. Threaded under manifest `75e8523c`.**

Refs: orchestration design `0d93f919` (directive grammar + hook + the config — the architecture; its pane-per-role is corrected here) | PM correction `c6c75cf9` (v1/v1.5 boundary) | routing config v1 `08b19eee` | superseded v2 `27b8337a` (one-window) | superseded v1 `f679c7db` (pane-per-role) | Area A `940cfbae` | matrix+configs `238b450b` | resolver spec `42022af0` | Area G seed `bf7b3379`.

## THE MODEL IN ONE LINE

ONE window. Roles are agents the ORCHESTRATOR spawns on demand and that self-register into the project. The agent you're talking to decides the next hop and emits a tradable guidance line in chat; a hook (when built) reads it and pre-fills `/resume <id>` into your input box; you press Enter; the next agent resumes, reads its plate from memsys, works, emits its own guidance. memsys is the shared state; the chat-emitted guidance is the routing contract; you are always the Enter.

## CORRECTION LINEAGE (so the drift is on the record)

- v1 `f679c7db`: pane-per-role, tiled, select-pane navigation. WRONG (inherited `0d93f919`'s "each role gets a pane").
- v2 `27b8337a`: one window, but framed as the HUMAN looking up session-ids and `/resume`-ing. Half-right (one window) but missed that AGENTS drive routing and the ORCHESTRATOR spawns roles.
- v3 (this): one window + orchestrator-spawned progressive roles + agent-emitted guidance + hook-prefills-via-tmux + human-Enters. This is the owner's actual design.

## A. PROJECT BOOTSTRAP

1. Human starts a new project **as PMO**. PMO scaffolds defaults: creates the team, the project manifest (the root all work threads under), baseline specs. The project exists in memsys before any role agent does.
2. Human discusses the idea with **PM** → a high-level goal + a draft of what's wanted, written to memsys.

## B. PROGRESSIVE, ON-DEMAND ROLE SPAWNING (the org grows as work demands)

The org is NOT a fixed six-pane grid up front. Roles are spawned when the human decides they're needed:
3. Human asks the **orchestrator to spawn a PA**. Orchestrator creates the PA agent (its own Claude Code session); the PA **self-registers into this project as PA** (writes its session into the session-registry + a role-registration). 
4. Human + PM decide to generate **domains**. The orchestrator creates a domain — primarily a **DO + DA pair, plus a developer** — each self-registering into the project.

So spawn-order follows need: PMO → PM → (spawn PA) → (spawn domain: DO + DA + developer) → (Reviewer when first review is needed). "Spawn a PA" / "create a domain" are themselves human-gated guidance (a `spawn_request`, config = `notify` per `08b19eee`): the human issues it; the orchestrator acts.

**The orchestrator is a first-class actor** — not just tmux+scripts. It SPAWNS agents (creates the session, loads the role prompt + on-resume prefix) and the spawned agent SELF-REGISTERS. (v1: "spawn" may be the human running a spawn script the orchestrator role hands them; full programmatic spawn is the automation target. The contract is: a spawn produces a registered, resumable role-session.)

## C. THE DEVELOPMENT LOOP (seamless, human-followed, agent-driven, single window)

5. For a real requirement, the human goes to **PM/PO**. It analyzes, writes what it needs to memsys (or tells the human what to do), and emits a guidance line — e.g. `/resume <PA-session-id>` — which the hook pre-fills into the input box.
6. Human presses **Enter** → PA resumes in the same window. Its on-resume prefix fires: load role-def + matrix/configs + Area A + manifest thread + run its default-list resolver → surfaces its plate. PA works.
7. PA, on completing its scope, emits the next guidance (`/resume <DO-id>` or DA or developer). Hook pre-fills; human Enters; that agent resumes and reads its plate.
8. Each agent, when done, **hands the human to the next agent** by emitting the next guidance line. The human is pulled forward one Enter at a time. DO → DA → developer → reviewer, each in the same window, each reading its plate from memsys on resume.

## D. THE ROUTING MECHANISM (guidance-emission + hook, governed by config `08b19eee`)

Two separable halves:
- **Guidance-emission (v1, prompt discipline, MANDATORY now):** each role prompt is written so that at completion-of-scope the agent emits a whitelisted, parseable guidance line in chat — the directive grammar from `0d93f919` (`NEXT: /resume <id>`, `ESCALATE: ...`, `DONE: <slug>`, `SPAWN: <role>`). The agent resolves the target's session-id from the session-registry to put in the line. No tooling required — the line is durable chat text.
- **The hook (the tmux layer, built later per `c6c75cf9`):** a post-response hook reads the agent's chat output, detects the guidance line, and per the routing config `08b19eee` either PRE-FILLS it via `tmux send-keys` WITHOUT Enter (`prefill` paths) or NOTIFIES passively (`notify` paths). **The human always presses Enter in v1** — never the hook (auto-Enter is a deferred per-path opt-in). Input-buffer safety net: pre-fill only into an idle prompt.

Graceful degradation (unchanged from `0d93f919`): the guidance line is in chat regardless of the hook. No hook / hook misfire → human reads the line and acts by hand. The hook only removes typing, never gates the flow.

## E. SESSION-REGISTRY (role → session-id)

ONE memsys memory per project, slug `pmo-project-<slug>-session-registry`, `type=decision`, evolves by supersede. Maps `role-code → {claude_session_id, registered_at, last_active}`. With one window and agent-driven routing, the registry is how an EMITTING agent resolves the next agent's session-id to put in the guidance line (and how a spawn records the new agent). No pane field (no panes). Distinct from per-work-item registration leaves (Area A §6 / T7: identity→work-item, "what's on my plate").

## F. ON-RESUME PREFIX (warm-start, unchanged in substance)

On resuming into a role: load role-def by slug + matrix/configs `238b450b` + Area A `940cfbae`; `memory_thread_get(manifest_root)`; run the role's default-list resolver (`238b450b`) for "what's on my plate"; report one line + await human. Matters most here because resuming-into-a-role is the routine action.

## G. PLATFORM / psmux

One-window + agent-driven needs only: a durable terminal session holding one Claude Code REPL + `/resume` + (for the hook later) `send-keys` to pre-fill the input. tmux, psmux, or any tmux-like send-keys/capture-pane substrate works (`0d93f919` names psmux/wmux for Windows). No pane/window management in v1. The hook's `send-keys` is the only substrate-specific dependency, and it's the v1.5 layer.

## SCRIPT INVENTORY (v1, one-window, pre-hook)

1. `pmo-session-up.sh <slug>` — create/attach the single-window session.
2. `pmo-spawn.sh <slug> <role>` — (orchestrator helper) start a role's Claude session in a resumable way + self-register it. Captures the spawn contract until programmatic spawn exists.
3. `pmo-register.sh <slug> <role> <session-id>` — record/refresh a role's registry row (supersede; session-id only).
4. `pmo-where.sh <slug> [role]` — read the registry (how an agent/human resolves a target's session-id).
5. `pmo-resume-hint.sh <slug> <role>` — print the `/resume <id>` line (the manual stand-in for the hook's pre-fill, pre-hook).
(`pmo-goto.sh` deleted — no panes.) The future hook (read-chat → prefill-via-send-keys per `08b19eee`) is the automation deliverable on top, not a v1 script.

## DEPENDENCIES / RISKS (unchanged)

- Reference-validation defect `bf7b3379`: degrades routing-by-traversal, not the registry/resolvers (tag/slug-based). E unaffected.
- R6-G1: PM default-list surfaces escalations but not formal ratifications until the DO tag-vocab decision; warm-start works.
- on_startup cross-tenant (`b20365e0`): irrelevant to E.

## STATE / NEXT

v3 supersedes v2 `27b8337a`. Open for owner confirmation. The directive grammar (the exact whitelisted guidance shapes incl. `SPAWN:`) and the orchestrator's spawn mechanism are the two things to pin next — they're the v1 prompt-contract + the spawn-contract the role prompts will embed. Scripts to be (re)written to this shape when wiring the demo machine. `0d93f919` remains the architecture-of-record for the directive grammar + hook; its pane-per-role is corrected by this v3 for the v1 substrate.
