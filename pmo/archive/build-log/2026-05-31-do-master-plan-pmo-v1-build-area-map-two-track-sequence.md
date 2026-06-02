---
source: memsys (team: pmo)
id: 141a9f5e-d65a-4e91-815f-138d3888fe98
type: decision
version: 1
is_current: True
created_at: 2026-05-31T09:54:46.963223Z
updated_at: 2026-05-31T09:54:46.963223Z
tags: [pmo, do-master-plan, pmo-project-pmo-v1-build, for-architect, for-pm, v1, awaiting-verification, current]
extracted_at: 2026-06-02
---

# DO MASTER PLAN — PMO v1 build (area map + two-track sequence)

**Written 2026-05-31 by DO (PMO domain). State: `awaiting-verification` — owner/PM to confirm top-level shape before DO authors the granular area specs. Closes the handoff loop from `3823d5bb` (DO accepts + proposed plan).**

Refs: handoff `3823d5bb` | architecture `121344a6` (locked) | orchestration design `0d93f919` (v1.5 automation; substrate promoted to v1 per correction) | PM response `a0cf2841` | PM addendum `d7a6c240` | PM tmux correction `c6c75cf9` | manifest root `75e8523c`.

## RECONCILED PM POSITION (later memories override earlier on conflict)

- **Demo = simulation** (`a0cf2841` Q1, firm): owner in tmux panes, role-prompts + raw memsys tools. Plugin is the deliverable BUILT, not the demo vehicle. Plugin proceeds behind the demo.
- **Demo target** (`d7a6c240`): build a real system with current prompts; role-responsibilities may be WIP; agents may err; **the process MUST NOT FAIL.** Only an *unrecoverable stuck state* = failure. Recoverable stumbles are the demo's content.
- **Batch-read bug: DOWNGRADED** (`d7a6c240` reverses `a0cf2841` Q2). Worker reportedly fixed `96464537`; ratify normally; NOT gating, NOT pre-foundation.
- **Q3 escalation-seam: DO's choice** (`d7a6c240` reopens `a0cf2841` Q3). DO chose **Option B (expose-during-demo) with a sealed fallback** — see Decision D1.
- **tmux: v1-MANDATORY** (`c6c75cf9`): substrate + session-registry + on-resume prompt-prefix + manual routing are v1. Only auto-routing tooling (hooks/send-keys/idle-detect/config) is v1.5.

## MODEL DELTAS vs THE WRITTEN SPECS (settled in DO dialogue this session; specs predate them)

1. **SIX roles, not four.** PM/PA own project-tier; DM/DA own epic-tier; Dev/Reviewer execute. The five written specs are 4-role and are superseded on ownership by the 6-role model. **OPEN:** long-form names of PA/DM/DA not yet pinned — carried as a fill-in for owner/Architect, NOT invented by DO.
2. **Work-item taxonomy is JUST-IN-TIME, per-tier, owned by each tier's role.** NOT a big upfront design milestone. PM authors project+milestones; DM/DA author epics when they engage; Dev authors stories/tasks. Forcing the full tree up front would violate role discipline.
3. **The only early taxonomy artifact is a THIN vocabulary convention:** level-names + ownership-map + `reference_kind` registry. Names and meanings, not schema.
4. **Linking uses 4 core mechanisms, each for a distinct job — nothing new enters memsys core:**
   - Hierarchy spine (story→epic→project) = `references` (`derived-from`), traversed both ways via `refs_in`/`refs_out`.
   - Registration discoverability ("what's on my plate") = tags (set membership, `memory_list` intersect).
   - Registration payload (session-id) + worklog/cursor = threaded working-memory under the work-item (write-fresh; sidesteps unshipped `update_in_place`; doubles as "where I left off").
   - General working content = `parent_id` threading under the manifest.
5. **Routing is direction-agnostic, ONE primitive:** resolve target's registration → write routed memory → invoke session. Upward=escalation, downward=nudge. **Pull-by-default** (architecture Decision 6); directed-nudge is an optimization layer.
6. **Registration stays INSIDE PMO** — composed from tags + threading + references. Does not cross the memsys boundary. (Tested against owner's rule: memsys serves generic core memory management; a need only goes back to core if generic across the whole ecosystem. Registration is PMO-shaped; the generic kernel it would need — queryable identity↔memory association — is already served by tags at demo scale.)
7. **One genuinely new design element** downward-flow surfaced: a **story state vocabulary** (`draft`/`ready`/`claimed`/`in-progress`/`done`) in tags, transitions owned by the tier-role. Concurrency-safe claiming deferred to v2 (single operator for the demo).

## THE AREAS (A–G)

**A. Vocabulary convention (THIN).** Level-names + ownership-map (6-role tiers) + `reference_kind` registry + `refs_version` policy per kind. One short artifact. Exists early ONLY because B/C refer to level-names and edge-kinds. NOT the full taxonomy.

**B. Role-on-work-item registration.** (identity, role, session-id) bound to a work-item. Discoverability via tags; session-id + cursor via threaded working-memory (Decision D2: threaded, not metadata). Supports global self-discovery ("what active tasks do I have, what's newly routed to me").

**C. Routing & escalation (the cross-role seam).** POC discovery by `references` traversal up the chain; routed-memory + session-invoke primitive, direction-agnostic. Resolves the `pending_intake` seam mismatch as ONE contract (Decision D1). Pull-by-default; directed-nudge optimization.

**D. Session lifecycle & resume.** Worklog/cursor ("where I left off"), on-resume prompt-prefix (v1, promoted), startup-invocation behavior (reads pending work + cursor → warm-start). Pairs with B/C.

**E. tmux substrate + tooling.** v1-mandatory: one session/project, pane/role, session-registry, Python listener + startup-invocation scripts driving tmux. Resume is `/resume <session-id>` (slash into a live REPL, cross-session — targets ANOTHER role's session via the registry), NOT `claude --resume`. Auto-routing stays v1.5.
   - **NOTE/correction to carry:** the tmux correction `c6c75cf9` and orchestration design `0d93f919` both wrote `claude --resume`; owner clarified the in-progress send-event is `/resume`. Minor grammar correction for the directive spec.

**F. PMO plugin proper.** The five written specs: infra (matrix+manifest+conventions+scaffolding) → four role tools. Proceeds BEHIND the demo. Areas A/B/C/G-state feed corrections into them (6-role expansion, registration model, escalation contract, story-state vocab).

**G. Demo readiness (simulation).** tmux up; a SEEDED work-item tree (real project→epic→story in memsys — routing-by-traversal needs something to traverse); role prompts clean enough to hand-simulate; batch-read green; sealed Q3 fallback note in place.

## DEPENDENCY SPINE

A (thin vocab) → B, C (need level-names + edge-kinds to refer to) → D, E (need B/C registration+routing) → G (needs a thin vertical slice of A–E working by hand). F (plugin) runs behind G, fed by full A–D designs. Story-state vocab (model delta 7) is a small sub-item of A/C that both B-discovery and C-routing depend on.

## TWO-TRACK STRUCTURE

**DEMO TRACK (time-boxed to Monday):** A-thin → B-thin → C-thin → D-thin → E → G. The simulation vertical slice. Built largely as conventions + scripts, NOT the full plugin. Governed by "process must not fail / recoverable-stumbles-are-the-content."

**PLUGIN TRACK (not time-boxed):** F. The durable build via the standard gate cycle (proposal→ratify→test-plan→ratify→implement→impl-response→ratify). Fed by the same A–D designs. Landing post-Monday is acceptable per PM.

The tracks share the A–D design work: design once, consumed by both the by-hand simulation and the plugin.

## OPEN DECISIONS (DO-made, recorded for transparency; flip on owner/PM word)

**D1 — Q3 escalation-surfacing seam: OPTION B (expose-during-demo) + SEALED FALLBACK.** Let the gap stand so the demo can show the framework recovering from its own spec gap (the on-message demonstration per the refined target). BUT DO pre-writes the resolution (consistent target-role list-key + escalation tag convention + manifest-threading) as a sealed note, applied only if live recovery would otherwise STALL — so "expose" can never become "unrecoverable." Keeps B honest while bounding the only true failure mode.

**D2 — Registration mechanism: THREADED registration-memory** (not metadata-on-work-item). Avoids the unshipped `memory_update_in_place` (session-id changes would force supersession on metadata); unifies registration with the worklog/cursor (one threaded "I'm-here + where-I-left-off" memory, written fresh each resume); tag on it carries discoverability.

## WORKER-CONTRACT REFINEMENT (process-hardening, NOT a build gate per owner)

Bake "every shipped PR includes an `awaiting-verification` impl-response memory as part of the deliverable" into the worker prompt. Folded in when convenient on the Plugin track; does not gate the Demo track.

## DO POSTURE / NEXT

This master plan is the top-level shape. On owner/PM confirmation, DO authors the granular artifacts: (1) the thin vocabulary convention (A), (2) per-area design specs for B/C/D + story-state vocab, (3) the milestone sequence for the Demo track, (4) the escalation-seam contract amendment (the sealed D1 note) referenced from the four role-tool specs. Architect is then engaged to plan B/C/D/E and F thoroughly. DO does NOT dispatch any worker before the Architect has planned and the owner has ratified the milestone sequence.
