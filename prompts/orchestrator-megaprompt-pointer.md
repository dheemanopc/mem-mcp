---
source: memsys (team: pmo)
id: 8702533e-9546-4e7f-8891-7ccf8994a913
type: fact
version: 2
is_current: True
created_at: 2026-06-01T18:07:18.332258Z
updated_at: 2026-06-01T18:07:18.332258Z
tags: [canonical-pointer, current, for-future-modules, for-pm, indexable-false, infrastructure, megaprompt, pmo, pmo-substrate, project-manifest, project:pmo-v1-build, v1.2]
extracted_at: 2026-06-02
---

# PMO Orchestrator Megaprompt v1.2 — Canonical Pointer (supersedes v1.1 pointer 99b35331)

**Type:** `fact` + `indexable=false`. Supersedes `99b35331` (v1.1 pointer). Companion to `b194ba79` (hook-fixes reference). Threaded under PMO manifest `75e8523c`. Records the v1.2 megaprompt: what changed, the new SHAs, and the verification recipe.

## WHAT CHANGED v1.1 → v1.2 (the four fixes applied this session)

1. **Fix #2 — explicit shared-artifact team (was a SILENT failure).** v1.1 STEP D pointed to the shared framework artifacts (matrix, role defs, grammar) by slug but assumed the pmo team. A project in a DIFFERENT team spawned roles that couldn't load their matrix, with no error. v1.2 STEP C asks for `<FW_TEAM>` (default `edc1a7f0-9f5a-4dd7-b045-ccd9a418b2d9`) and `<PROJ_TEAM>`; roles load shared artifacts by (team=<FW_TEAM>, slug); project memories go in <PROJ_TEAM>. The manifest records a POINTERS block naming <FW_TEAM> + the shared slugs.

2. **Fix #3 — STEP B now NUKES a pre-existing project-level Stop hook (F1 prevention).** v1.1 only warned. v1.2 STEP B checks `$PWD/.claude/settings.json`, backs it up, and deletes any `hooks.Stop` before wiring the user-level hooks. Prevents the duplicate-config double-fire (F1) when bootstrapping in a project that already had Claude Code hooks.

3. **Hook fixes #F1–F6 carried (from b194ba79).** The embedded Stop hook is the fixed version: flush-race settle+poll (F2), bracketed-paste split-keystroke auto-Enter (F3), dirty-buffer wipe (F4), idempotency guard (F5), dual-grammar parse v2+v3 (F6).

4. **Fix #4 — psmux support (Windows).** The Stop hook now has a MUX abstraction (PMO_MUX / autodetect tmux→psmux). psmux pane-discovery uses psmux list-panes by PID (NO /proc dependency, since Windows has no /proc). The session-start hook is mux-agnostic (pure registry write). The Python adapter (pmo_mux.py) already had PsmuxMux. **CAVEAT: the psmux path is IMPLEMENTED but UNVALIDATED on a real psmux/Windows box — must be tested there before trusting Windows OOTB. Mac/Linux+tmux is the validated path.**

## THE v1.2 ARTIFACT

- **Logical name:** `pmo-orchestrator-megaprompt.md` (v1.2)
- **Size:** 38,564 bytes
- **SHA-256:** `01e70cc45cc6f66571eaf1fd97c55fc1ad10f964d78719fd70d46a71c3e73a21`
- **Embedded `pmo-stop-hook.sh` (v1.2) SHA-256:** `37af71f49611ba34e61ad8acd638c31fe3160fc0304877cd8cd51b5dcd873ce5`
- Round-trip verified bytes-for-bytes: the embedded base64 reconstructs the v1.2 hook + pmo CLI identically, and `pmo` runs from the reconstruction.

**PROVENANCE CAVEAT (important):** this v1.2 was rebuilt in a PMO-build assistant session, NOT in the live consuming-project environment that produced v1.1. The v1.2 embedded hook is a fresh authored version carrying F1–F6 (reconstructed from b194ba79's descriptions) PLUS psmux — so it is NOT byte-identical to the live v1.1 hook (`fe6a4381…`) and will not match that SHA. v1.2 SUPERSEDES v1.1; it does not claim to match it. Before adopting v1.2 on the live machine, diff its behavior against the validated v1.1 hook on a real tmux routing test (the Section-4 checklist in b194ba79), since v1.2's hook has not yet had a live routing run.

## VERIFICATION RECIPE
```
sha256sum pmo-orchestrator-megaprompt.md
# v1.2 canonical: 01e70cc45cc6f66571eaf1fd97c55fc1ad10f964d78719fd70d46a71c3e73a21
```
Extract + hash the embedded hook:
```
sed -n '/^base64 -d > "pmo-stop-hook.sh"/,/^B64_pmo_stop_hook_sh$/p' pmo-orchestrator-megaprompt.md \
  | sed -e '1d' -e '$d' | base64 -d | sha256sum
# must equal: 37af71f49611ba34e61ad8acd638c31fe3160fc0304877cd8cd51b5dcd873ce5
```

## STILL OPEN (carried from v1.1)

- **Canonical LOCATION (Fix #1) — still undecided.** v1.2 megaprompt currently lives in the build-session output, not a permanent home. RECOMMENDATION unchanged: store the full body in memsys as `fact + indexable=false`, slug `pmo-megaprompt-v1-2-body`, written FROM the machine that holds the verified file (so the stored bytes match the SHA). Until then, a new project still has no canonical pull source — this is the #1 thing blocking true OOTB and is the owner's call (memsys body / git repo / object store).
- **README/INSTALL doc drift:** README still describes pre-fill+human-Enter; the deployed hook auto-Enters (F3). Doc needs the auto-Enter correction wherever the canonical copy lives.
- **psmux/Windows validation (Fix #4):** implemented, not yet run on real psmux.
- **v1.2 hook has not had a live routing run** — see provenance caveat; run the b194ba79 Section-4 checklist before trusting it on the live project.

## CROSS-REFS
- v1.1 pointer (superseded): `99b35331`
- hook-fixes reference: `b194ba79`
- first consuming project debug write-up: `35a097eb` (school-homework-app team)
- PMO manifest: `75e8523c`

End of v1.2 pointer.
