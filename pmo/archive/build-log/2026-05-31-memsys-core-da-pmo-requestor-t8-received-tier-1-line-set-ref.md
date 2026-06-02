---
source: memsys (team: pmo)
id: 1ad85195-88d1-4b74-a837-7d363530b294
type: decision
version: 1
is_current: True
created_at: 2026-05-31T16:56:38.353823Z
updated_at: 2026-05-31T16:56:38.353823Z
tags: [current, for-da, for-developer, infrastructure, memsys-core, memsys-core-da-to-pmo, pmo, project-manifest, references-folded, resume-condition, t8-handoff]
extracted_at: 2026-06-02
---

# memsys-core DA → PMO (requestor) — T8 RECEIVED, Tier-1 line set, `references` FOLDED IN (your T5 spine is covered). Resume condition below.

**Written 2026-05-31 by memsys-core DA. State: `for-da` (PMO DA) + `for-developer` (PMO Developer). Threaded under PMO manifest `75e8523c`. Platform-side reply to the T8 gap dossier `bbff47cb` and gap-sweep program `101ce273`. The authoritative memsys-core spec is DA artifact `a1cc3f15` (memsys-core team, under manifest `27666e06`); this memo is the cross-boundary handoff so the PMO track knows what is coming and when to resume.**

Refs (in-body): PMO T8 dossier `bbff47cb` | gap-sweep `101ce273` | PMO-track-parked `1f118c0e` | memsys-core DA artifact `a1cc3f15` (spec of record) | write-gap `3d1145c7` | read-gap `92d09e06` | on_startup impl-response `b20365e0` | task set `4eb18941`.

## VERDICT ON YOUR DOSSIER
Received, verified. Your live-status sweep was correct; the dossier is the input we authored from. memsys-core takes T8 end-to-end (spec → plan → PR → deploy) per the parked plan `1f118c0e`. PMO does NOT author the core PR. Hold, then re-verify and resume when the extended SDK lands.

## TIER-1 LINE memsys-core IS SHIPPING (what you get back), one PR:
- `write` += `parent_id`, `indexable`, **`references`** ← the change from your deferral, see below
- `thread_get` (T3)
- `get_batch` incl. slug-tuple (T3, T4 — substrate slug-tuple path shipped `96464537`)
- `list` with tags + `indexable` + **`team_id`** (T3 working-leaf retrieval; `team_id` closes your F-7)
- `update` wrapper over shipped `memory_update` (T6 — restriction below)
- error-surfacing fix so rejections are visible (your F-6 + F-9, folded — load-bearing for `update`/`references`)

## THE ONE CHANGE FROM YOUR DOSSIER — `references` is NO LONGER DEFERRED
Your dossier filed `references` as SF-7-deferred ("references vs supersede, decide at T5 plan time"). PL confirmed this session that PMO task types connect BY reference — the `derived-from` task graph is structural, not an optional lineage choice. So memsys-core folds `references` into Tier-1, NOT a second PR. **T5's reference spine is covered by the same extension.** Do NOT design T5 around `memory_supersede` as a references-substitute; write structured `references` on the formal-artifact write directly (your T5 DoD as originally framed in `4eb18941` — `derived-from`, `memsys_refs_out`/`refs_in`, refuse-on-failure, hard-delete-block — all hold against the live surface).

Why it was safe to fold (for T5 design): substrate `resolve_reference_target` (ratified `70b43c08`) already access-checks the target's team and returns the opaque IT-08 error cross-team. `references` is a read-gated link, access-enforced below the SDK — exposing it doesn't widen the trust boundary. Practical notes for your Developer: (a) cross-team reference targets opaque-reject; same-team resolve. (b) Watch async index-drain timing — a freshly-written target can transiently fail the ref-existence check, as your DA already hit in `27824fcb`; write the target, let it drain, then write the citer.

## T6 update-verb — RESOLVED, `update` IS in, with a restriction to design to
Your §E surfaced write-fresh-vs-in-place now that `memory_update` shipped. PL ruled: expose in-place `update`. BUT the SDK `update` inherits the shipped tool's restriction unchanged — **content edits only on `note`/`snippet`/`question`; `decision`/`fact` content goes through `supersede`; tags/metadata editable on all types.** So T6 verb dispatch: working-memory update → `update` (cheap in-place); formal-artifact revision → `supersede` (lineage preserved). This is consistent with your `4eb18941` T6 DoD ("formal updates supersede; working updates write-fresh") — except working updates can now be true in-place `update` instead of write-fresh, which keeps the manifest thread cleaner. Your call at T6 plan time which to use for working; both are available. Audit spine on decisions/facts stays intact either way.

## DEFERRED (so you don't wait on it) — cross-team write
`visibility` + `team_id` on the write surface are NOT in either tier. PL deferred the cross-team-write authorization model (likely shape: cross-team write becomes a role-granted capability, not a write-call field). PMO writes `team`-scoped only — does not block any PMO task (all your work is in the `pmo` team). If a future PMO need wants cross-team write, raise it and PL opens the brainstorm.

## RESUME CONDITION (unchanged from `1f118c0e`, now with a concrete surface)
1. memsys-core: plan → PR → owner merges + deploys the Tier-1 SDK extension to prod.
2. **PMO Developer re-verifies** the dossier's live-status stamps against settled prod HEAD (team still shipping — re-verify, don't trust stamps).
3. T2 implements (write-gap closed), T3 plans/implements (read-gap closed), **T5 builds the reference spine** (references now available), T6 composes on all of it.
4. PMO DA confirms live surfaces at each task's verification gate.

Until deploy: T2 paused, T3 holds, no core PR from PMO. We signal on this thread when the extension is live.

## `on_startup` — RATIFIED VERIFIED
Your shipped `on_startup` change (`b20365e0`, PR #298) is ratified VERIFIED in DA artifact `a1cc3f15`. Gap `36ac16a1` closed. T7 can rely on the hook — with the cross-tenant ctx constraint: startup ctx is `tenant_id == NIL_UUID`, per-tenant clients raise; enumerate tenants via `system_tx(ctx.pool)` for per-tenant startup work, same as plugin jobs. Your `e4e61a71` lean (explicit-tool-call registration for T7) remains valid and is no longer forced by a gap — it's now a T7 design choice.

## SPEC OF RECORD
Full parity matrix + Tier-1 spec + on_startup ratification: memsys-core DA artifact `a1cc3f15` (memsys-core team, under manifest `27666e06`). This memo is the PMO-track summary; that artifact is what the memsys-core Developer builds from. Coordinate cross-track through Product Leader. memsys-core DA available for boundary seam questions.
