---
source: memsys (team: pmo)
id: 101ce273-f041-4fde-8b21-29ecb70bc392
type: decision
version: 1
is_current: True
created_at: 2026-05-31T15:49:16.675290Z
updated_at: 2026-05-31T15:49:16.675290Z
tags: [current, da-to-developer, da-to-do, for-developer, for-do, gap-sweep, infrastructure, memsys-core, pmo, pmo-project-pmo-v1-build, project-manifest, t8-gap-dossier, v1, verify-live-status]
extracted_at: 2026-06-02
---

# DA DIRECTION — Commission full T2–T7 LLD gap sweep → DA authors T8 (core-extension task) → Developer plans all tickets → one PR → memsys-owner review + owner-thread deploy. VERIFY each defect's LIVE status before it joins the fix agenda.

**Written 2026-05-31 by DA (PMO domain), threaded under manifest `75e8523c`. State: `for-developer` (primary) / `for-do` (tracking). Sets the front-loaded gap-analysis program per PM/owner direction this session: stop discovering SDK gaps reactively task-by-task; sweep T2–T7 LLD up front, consolidate into a single memsys-core extension task (T8), plan all tickets together, ship as one owner-reviewed PR. Supersedes the piecemeal core-gap filing approach.**

Refs: PM/owner direction (this session) | T2 blocker `25eab7f6` + DA direction `6d43a262` | T3 pre-planning gap `92d09e06` | Developer write-gap filing `3d1145c7` (CANONICAL) | DA duplicate write-gap filing `d287353a` (TO BE FOLDED — see dedup below) | task set `4eb18941` | open core items `96464537` / `9bac15e4` / `36ac16a1` / `edeae913` | SF-1..SF-7 (`584614ac` + `6d43a262`).

## WHY THIS PROGRAM (the correction)
Reactive per-task gap discovery has cost two stalls: T2's write-gap (impl-time) and T3's read-gap (pre-plan). Both trace to one root cause — the Plugin SDK `MemoryClient` exposes a thin subset of what the substrate supports. Front-loading the full gap surface across T2–T7 lets us extend the SDK ONCE, in one reviewable+deployable unit, instead of discovering and patching task-by-task.

## STEP 1 — DEVELOPER: full LLD gap-analysis dossier, T2 through T7, one by one
Author a single dossier memo (`for-da`, `t8-gap-dossier`) that walks EACH task T2–T7 and, for each, enumerates every memsys-core / Plugin-SDK capability its DoD requires vs what the SDK exposes today. Known seeds (NOT exhaustive — the sweep must find the rest):
- T2: `write(parent_id, indexable)` — the write-gap (`3d1145c7`).
- T3: `thread_get`, `get_batch` (incl. slug-tuple), `list` (tag-filter returning `indexable=false` rows) — the read-gap (`92d09e06`).
- T4: matrix read at startup — confirm whether `get`/`search` suffice or a dedicated read is needed.
- T5: `references` on the write surface (the SF-7 deferred question) vs `memory_supersede` lineage — surface it, do NOT resolve it (T5-design call).
- T6: every working/formal write+update path through the engine — confirm it composes on the T2/T4/T5 surfaces with no new gap.
- T7: registration write + self-discovery — and the KNOWN `on_startup`-never-invoked gap (`36ac16a1`) which T7/Area-D may assume fires.

For each gap: the exact SDK/Protocol surface needed, the underlying substrate support (file:line if it already exists at the tool layer), and whether it's a NEW capability vs an already-filed item.

## STEP 1a — MANDATORY: VERIFY LIVE STATUS BEFORE ANY DEFECT JOINS THE FIX AGENDA
**The memsys team is actively deploying fixes. Our on-file records may be stale.** Before the dossier lists ANY defect/gap as needing a fix, the Developer MUST verify its CURRENT state against the live system (read memsys-core HEAD for the relevant file:line; for runtime defects, check prod/deploy state), NOT against the PMO-side memory record. Concretely:
- `96464537` (`memory_get_batch` slug-tuple crash) — task-set substrate-facts (`4eb18941`) claims the slug-tuple fix is "confirmed on prod," but `96464537` itself is `awaiting-verification` with the crash open. CONTRADICTION on file. Verify live which is true before T3/T8 depend on it.
- `36ac16a1` (`on_startup` not invoked), `edeae913` (bootstrap), `9bac15e4` (`memory_update_in_place` spec-only) — re-verify each against current core HEAD; any the memsys team has since shipped DROPS OFF the T8 agenda.
- The write-gap (`3d1145c7`) and read-gap (`92d09e06`) — re-verify the SDK surface is still as captured (the team may have already extended it).
Each dossier entry carries a "verified-live-as-of <timestamp>, state: {open|shipped|in-flight}" stamp. Anything already shipped is recorded as closed, not re-filed.

## STEP 1b — DEDUP TO ABSORB
The write-gap was filed TWICE: Developer `3d1145c7` (15:16, canonical) and DA `d287353a` (15:34, my duplicate — I filed without seeing yours first; my miss). The dossier treats `3d1145c7` as canonical; `d287353a` will be superseded/folded. Do not list the write-gap twice.

## STEP 2 — DA: author T8 from the dossier
Defining a task is DA's structural authority (intent/structure seam, `2b256cad`), so DA — not the Developer — authors T8 (scope, DoD, granularity, spine placement) FROM the Developer's dossier. T8 = "memsys-core Plugin-SDK extension: surface the read+write capabilities the working-memory mechanism needs." T8 sits as a dependency the PMO spine blocks on, consolidating the verified-open gaps into one coherent task. (If owner prefers Developer-drafts-T8 / DA-ratifies instead, say so — default per role lock is DA-authored.)

## STEP 3 — DEVELOPER: plan all T8 tickets in one go
Once T8 is authored, the Developer produces the implementation plan covering ALL the SDK additions together — one plan, the full set of method/kwarg additions, deps-of-deps in one motion (the Developer's own lean in `92d09e06`). We (DA + owner) review the plan.

## STEP 4 — PR → memsys-owner review → owner-thread deploy
One PR against memsys-core implementing the planned T8 tickets. Reviewed by the memsys-core OWNER (this is core, gated by core's owner — correct). Owner thread deploys. On deploy: SF-6..SF-10 describe the now-live surface; T2 and T3 resume against it unchanged; DA confirms live surfaces at each task's verification.

## AUTHORSHIP OF THE CORE PR (owner decision, carried)
Owner steer this session: allow the Developer to author the core PR (a scoped, owner-reviewed core change — the explicitly-authorized option DA flagged in `6d43a262`, NOT a smuggled-in PMO-repo change). This is consistent with repo-placement discipline (`50e11ec8`) because it is an authorized core-repo PR, not the PMO plugin repo mutating core opportunistically. One author (Developer), owner reviews+merges+deploys. Confirm if you intended otherwise.

## SF ADDENDUM STATUS
SF-6 locked (`6d43a262`). SF-8/SF-9/SF-10 (thread_get / get_batch / list) PROPOSED in `92d09e06` — DA will ratify or amend their exact shapes when authoring T8 from the dossier, not before. SF-7 (`references`) stays deferred to T5.

## SPINE EFFECT
T8 does not reorder T1–T7's logic; it consolidates the core dependency they all share. T2 stays paused (`27824fcb`); T3 planning may proceed against the target surface in parallel (per `c33ae22b`), OR hold for T8 if owner prefers a clean single-resume — flagging the choice, leaning "let T3 plan proceed, it sharpens the dossier."

## ASK / NEXT
Developer: build the T2–T7 LLD gap dossier with the Step-1a live-status verification stamped on every entry; fold the `3d1145c7`/`d287353a` dedup; route back `for-da`. DA authors T8 from it. Do NOT begin the core PR until T8 is authored and its plan reviewed.
