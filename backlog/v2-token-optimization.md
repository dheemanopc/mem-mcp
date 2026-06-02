---
source: memsys (team: pmo)
id: 9d1a6a18-9fd1-4bd6-8af5-4980c153dcdf
type: decision
version: 1
is_current: True
created_at: 2026-06-01T05:33:47.374058Z
updated_at: 2026-06-01T05:33:47.374058Z
tags: [pmo, v2-backlog, token-optimization, for-pm, for-da, pmo-project-pmo-v1-build, current]
extracted_at: 2026-06-02
---

# PMO v2 — Token optimization (framework-wide) is a v2 workstream

**Written 2026-06-01 by DO (PMO domain), threaded under manifest `75e8523c`. State: `v2-backlog` / `for-pm` / `for-da`. Owner direction: the single-memory matrix+configs token cost is ACCEPTABLE for the demo/simulation path in v1; defer to v2. AND, more broadly, OVERALL token optimization is a v2 workstream — not just this one item. Sibling to v2 items `ea4aff7e` (setup-generator) and `23259bee` (test-env feature request). This is the SECOND PMO v2-backlog entry.**

Refs: matrix+configs `238b450b` (the immediate instance) · Area A `940cfbae` · config/matrix schema `7dcbb2c8` (single-memory, startup-cached v1 design) · T3 role-scoped load amendment in `7dcbb2c8` · setup-generator v2 `ea4aff7e`.

## OWNER DIRECTION (recorded)

The matrix+configs being one memory carrying all six role-configs is fine as-is for the DEMO PATH in v1. The token cost there (a simulation role session loading the whole document when it only needs its own slice) is acceptable for now. Mark for v2. AND treat OVERALL token optimization — across the whole framework, not just this artifact — as a v2 concern to be taken up as a workstream.

## WHY THE v1 COST IS ACCEPTABLE (the bound)

- **Plugin path: already optimal.** The matrix+configs is loaded ONCE at plugin startup into an in-process cache (T4); per-action `check_permission` is a dict lookup — zero memsys round-trips, zero per-action tokens. The single-memory design costs only a one-time parse. No v2 change needed for the plugin path.
- **Simulation/demo path: the only place it bites.** A role running as a Claude session loads context at session-start; if it pulls the whole matrix+configs (~5KB, six configs) when it needs only its own config slice + the shared matrix, that's wasted context tokens. Acceptable for the demo (single operator, bounded session count); not where v1 should spend effort.

## THE v2 WORKSTREAM (candidate optimizations — for v2 scoping, not now)

A non-exhaustive list of where framework token cost can be reduced; v2 planning prioritizes:

1. **Split human-prose from machine-data in loaded memories.** Several DO/DA artifacts (this matrix memory, Area A) bundle design notes + validation reports + commentary WITH the machine-relevant data, for reviewability. For runtime loading, the loaded memory should be the minimal data only; prose moves to a separate non-loaded memory. Roughly halves per-load size for these artifacts.
2. **Per-role config split.** The matrix (enforcement table) is irreducibly whole (cross-role routing checks other roles' cells). But the six CONFIG blocks are separable; a session needs only its own. v2: split configs into per-role memories (or a sliced load) so a session loads matrix + its-own-config, not all six. Pairs with the already-designed role-scoped session-load (T3 amendment).
3. **Session-start bundle minimization.** Audit what each role actually loads via `memory_get_batch` at session-start (role-def + matrix + configs + manifest thread + refs). Trim each to the minimal working set; lazy-load the rest on demand. The manifest thread especially can grow large (the demo thread is already 400KB+); a role rarely needs the whole history — recent + relevant slices suffice.
4. **Working-memory discipline already helps** (PM ruling `ba6d113a`: don't write self-notes). Fewer redundant working memories = smaller threads = cheaper loads. v1 already banks this; v2 can measure it.
5. **Vocab-as-data** (raised this session): collapse the machine-relevant slices of Area A (role codes, tag schemas, state values) into a small cached `pmo-vocab` data memory the engine loads once, leaving Area A as pure prose. One cached source for mechanical names; zero drift; smaller per-session prose load. Pairs naturally with the setup-generator (`ea4aff7e`), which would emit such a data memory anyway.
6. **Reference-slice loading.** When a memo cites another memory, load only the cited fragment/slice, not the whole target (the `target_fragment` mechanism exists; v2 could use it systematically).

## SCOPE FENCE

NOT v1. v1's remaining work is unchanged: D1 sealed-fallback note + `e153cdb8` answer (DO content close-out), then the demo/runtime track (tmux substrate E + B/C/D conventions + G readiness). Token optimization is explicitly deferred so it doesn't expand v1 scope. This memo exists so the intent — framework-wide token efficiency as a deliberate v2 workstream, with the candidate list above as its starting backlog — is captured, not lost.
