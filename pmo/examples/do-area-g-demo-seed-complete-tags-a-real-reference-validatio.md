---
source: memsys (team: pmo)
id: bf7b3379-cf6f-46e1-a7fb-d807bf434cd9
type: decision
version: 1
is_current: True
created_at: 2026-06-01T08:39:04.182086Z
updated_at: 2026-06-01T08:39:04.182086Z
tags: [pmo, do-to-da, do-to-developer, for-da, for-developer, for-pm, area-g-seed, demo-seed, reference-validation-defect, pmo-project-pmo-v1-build, v1, current]
extracted_at: 2026-06-02
---

# DO — Area G demo seed COMPLETE (tags) + a real reference-validation DEFECT surfaced (spine edges blocked)

**Written 2026-06-01 by DO (PMO domain), threaded under manifest `75e8523c`. State: `for-da` / `for-developer` / `for-pm`. Reports the Area G seeding the owner requested: the work-item tree + working leaves are seeded and resolver-discoverable by TAG (which makes T6's live default-list provable). The hierarchy SPINE EDGES (`derived-from`) could NOT be written — a write-time reference-validation defect rejects valid, readable targets. Surfaced per surface-don't-absorb, NOT worked around silently. This is itself good demo content (the framework catching a substrate regression), but it's a real gap to route.**

Refs: resolver spec `42022af0` · Area A `940cfbae` · matrix+configs `238b450b` · D1 `ced035fd` · R6-G1 named gap `1debfbee` · DO master plan Area G `141a9f5e`.

## WHAT WAS SEEDED (all in pmo team, project tag pmo-project-pmo-v1-build)

A real, shallow vertical slice — Agent Edge Dating (a genuine owner project, so the demo builds something authentic):

| Memory | Level | UUID | Owning-role tag (resolver surface) | State |
|---|---|---|---|---|
| Agent Edge Dating | project | `e6d93b67` | pm + pa | in-progress |
| Blind Date MVP | milestone | `9c1e8c11` | pa (→ pa_pending_structural) | ready |
| Anonymous matching | epic | `c3d03620` | dm (→ dm_pending_intake) | ready |
| Profile creation without PII | story | `e0c79357` | dm | ready |
| Bidirectional match surfacing | story | `e74edbd8` | dm | claimed |
| pgvector bidirectional search query | task | `453b141c` | developer (→ developer_assigned_tasks) | in-progress |
| developer↔task registration | reg leaf | `b789e0d2` | registration + identity-…0001 (→ developer_assigned_tasks) | in-progress |
| escalation → DA | escalation | `90600aa2` | escalation-to-da + open (→ escalation_for("da")) | open |

**Resolver coverage achieved by tags:** pa_pending_structural → milestone `9c1e8c11`. dm_pending_intake → epic + 2 stories. developer_assigned_tasks → task via registration leaf `b789e0d2` (identity 0a17e000-…-0001). da_pending_structural_ratifications secondary + (pm/dm intake unions) → escalation `90600aa2`. So EVERY tag-and-registration-based resolver returns ≥1 real row — T6's live default-list is now demonstrable on prod the moment a session loads. (pm/pa/reviewer PRIMARY ratification/review surfaces return empty until a real gate-cycle artifact exists with the queried tag — expected, not a seed gap; and note R6-G1 below for pm_pending_intake's ratification primary specifically.)

## THE DEFECT (spine edges blocked — route this)

The `derived-from` hierarchy edges (child work-item → parent, for `refs_in`/`refs_out` traversal routing) could NOT be written. Every `memory_write` carrying a `references` entry was rejected:
`-32602 reference target not found or not accessible / memory_not_accessible`

**Characterized it — it is NOT recency and NOT seed-specific:**
- Target `e6d93b67` rejected as a ref target, yet `memory_get(e6d93b67)` returns it fine (readable, committed, indexable=true).
- Retried via slug-tuple (`pmo-seed-project-agent-edge-dating`) — same rejection (different resolution path).
- DIAGNOSTIC PROBE: referenced Area A `940cfbae` (a decision committed HOURS ago, fully indexed) — **also rejected.**
- Both `indexable=true` decision writers and `indexable=false` note writers hit it — so it's not the indexable flag.

**Conclusion:** the write-time reference access-check is broadly rejecting valid, readable targets in this tenant/team right now. This mechanism WORKED earlier in the project (T5 reference-spine was built + verified on it; SF-15 ratified refs_in/refs_out). So this looks like a REGRESSION, plausibly from a recent memsys-core deploy (PR #300 Tier-2 SDK or PR #302 test-env) touching the reference validator or its access-scope resolution. Candidate hypotheses for memsys-core: (a) the ref access-check resolves target visibility against a stale/empty scope (echoes the earlier reminders RLS empty-GUC bug pattern the DA flagged in `e4e61a71`); (b) the validator's team-scope no longer matches the caller's team for same-tenant targets.

## IMPACT / WHAT IT DOES AND DOESN'T BLOCK

- DOES NOT block the resolver demo: discoverability is by TAG, fully seeded. T6 default-list works.
- DOES block: routing-by-TRAVERSAL (the `refs_in`/`refs_out` walk up the hierarchy spine), which is one of the four linking mechanisms (master plan delta 4) and underpins escalation discovery by chain-walk. For the demo, traversal can fall back to tag/parent-thread discovery (the spine edges are an optimization over tag-membership, not the only path) — so the demo is still runnable, but a designed mechanism is degraded.
- DOES warrant a memsys-core check BEFORE the live demo, because T5's verified reference-spine writer would hit the same wall in real use.

## ASKS

- **Developer/DA:** can you reproduce on a fresh `references` write (any valid target) from the plugin SDK path? If T5's `MemoryClient.write(references=...)` also now fails on prod, that's a regression against the SF-15-verified surface and should be filed as a memsys-core gap immediately (sibling to the test-env FR `23259bee`).
- **DO will:** file the memsys-core gap once Developer confirms SDK-path reproduction (so the report carries both the tool-path and SDK-path evidence), and add the spine edges as a one-shot pass once the validator is fixed (the work-items are all in place; only the edges are owed).

## SEED STATUS: FUNCTIONALLY COMPLETE for the resolver/default-list demo; spine-edge traversal owed pending the reference-validation fix. Not silently absorbed — routed.
