---
source: memsys (team: pmo)
id: 1f118c0e-8b3b-4de9-bffe-e2a1651bcd95
type: note
version: 1
is_current: True
created_at: 2026-05-31T16:36:27.812322Z
updated_at: 2026-05-31T16:36:27.812322Z
tags: [current, da-to-developer, da-to-do, for-developer, for-do, infrastructure, memsys-core, pmo, pmo-project-pmo-v1-build, pmo-track-parked, project-manifest, t8-deferred, v1]
extracted_at: 2026-06-02
---

# PMO TRACK — PARKED on memsys-core internal SDK work. T8 deferred to memsys-core; PMO does not author the core PR.

Written 2026-05-31 by PMO DA, manifest 75e8523c. State: for-developer / for-do. Per owner: memsys-core is taking the SDK-extension specs (write parent_id+indexable, thread_get, get_batch, list; on_startup 36ac16a1) through its OWN internal process and will implement them. This supersedes the earlier plan of a PMO-authored core PR.

WHAT CHANGES:
- T8 (SDK extension) is now memsys-core-owned end to end — internal spec + implement + deploy. PMO does NOT author the core PR. The Developer does NOT start core code. The gap dossier (bbff47cb) is the input PMO hands over; memsys-core's product architect owns the spec shape from here.
- T2 stays PAUSED (27824fcb), T3 HOLDS — both resume when the extended SDK lands on prod.
- DA does not author T8 now; authoring against a surface memsys-core is actively reshaping would go stale (friction F-2).

RESUME CONDITION: extended Plugin SDK lands on memsys-core prod → Developer RE-VERIFIES the gap dossier's live-status stamps against settled HEAD → T2 implements, T3 plans, against the now-live surface → DA confirms surfaces at each verification gate.

DEVELOPER / DO: hold. No core PR, no T8 planning, no T2/T3 implementation until the SDK extension is live and re-verified. Standing by.
