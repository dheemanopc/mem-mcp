---
source: memsys (team: pmo)
id: eec72021-923c-4549-b60a-c6ae3ec22038
type: decision
version: 1
is_current: True
created_at: 2026-05-31T13:49:15.290923Z
updated_at: 2026-05-31T13:49:15.290923Z
tags: [current, da-to-do, for-do, infrastructure, pmo, pmo-project-pmo-v1-build, pmo-task-T2, project-manifest, t2-scope-question, to-po, v1, working-memory-policy]
extracted_at: 2026-06-02
---

# DA → DO — escalate: does "working memories no longer required" change T2 scope? (for DO → PO)

**Written 2026-05-31 by DA (PMO domain), threaded under manifest `75e8523c`. State: `for-do`. Owner (PM) raised a working-memory-requirement flag against the T2 spec and directed it to DO, then PO. This is an INTENT/SCOPE question, not structural — DA is surfacing and routing, not resolving. DA's T2 structural gate is HELD pending the answer.**

Refs: DA task set `4eb18941` (T2 spec) | user-response capture `d1ceccf4` (verbatim owner words) | DA verification ratification `584614ac` (T1 verified, SF-1..SF-5) | DA→DO handoff `d0604273`.

## WHAT THE OWNER RAISED
While asking DA to check for a Developer T2 response, owner flagged: working memories are "no more required, as we are able to retain session." Owner then directed: "Take this to DO, and we will take it to PO." Verbatim in `d1ceccf4`.

## WHY THIS LANDS ON T2 SPECIFICALLY
T2 (per `4eb18941`) IS the working-memory helper library — the shared write path every role uses: auto `indexable=false`, standard tag set, threads under manifest via `parent_id`, `memory_write_async`, plus `pmo-user-response` verbatim capture. If the requirement for working memories changes, T2's scope changes. So the flag is not a side note — it sits directly upstream of the T2 build decision.

## THE AMBIGUITY DA WILL NOT RESOLVE UNILATERALLY (intent call → DO/PO)
"Working memories no longer required" reads two ways:

- **(a) Narrow — stop writing SELF-DIRECTED notes.** A session now retains its OWN history, so a role writing notes purely for its own future self is redundant. Under this reading, T2 is UNCHANGED: its entire DoD targets the CROSS-ROLE bridge (tagged leaves another role retrieves via `memory_list`) and `pmo-user-response` capture — neither of which session retention replaces.
- **(b) Broad — roles should stop persisting working state generally.** Under this reading, T2 genuinely shrinks, possibly to just `pmo-user-response` capture, and the T1–T6 spine/DoDs need amendment before the Developer plans against T2.

## DA's STRUCTURAL POSITION (input to the intent decision, not the decision)
Session retention is PER-SESSION and PER-ROLE. It gives a single role continuity within its own window. It does NOT bridge across roles: the Developer's session cannot see DA's dialogue, DO's cannot see the Developer's, etc. The ONLY reason this DA window could resume T1 state was that the cross-role content lived in memsys. So:
- Cross-role hand-offs, ratifications, and structured directives MUST still be persisted regardless of retention — they are the coordination substrate, not self-notes.
- `pmo-user-response` capture MUST stay (owner kept it required in this session's own framing).
- What retention plausibly makes redundant is ONLY self-directed status/reflection notes — which were never the thing T2 builds infrastructure for.

Net DA lean: reading (a) costs nothing and T2 proceeds unchanged; reading (b) is a real re-scope and needs ratification. DA recommends the answer come back as an explicit pick between (a) and (b), with — if (b) — a one-line statement of what working-memory classes survive, so DA can re-confirm T2's structural shape against it.

## ASK OF DO
Take the (a)/(b) question to PO with the owner. Return the resolution (routed `for-da`) as either: "(a) — T2 unchanged, proceed," or "(b) — T2 re-scoped to {…}; amend `4eb18941` T2 DoD accordingly." DA holds the T2 structural gate until then.

## STATE OF THE T2 GATE RIGHT NOW (for the record)
No Developer T2 response exists in the store: `pmo-task-T2` tag-list is empty; newest `for-da` items all predate the T1-close checkpoint (`71657418` T1 submission, `11050957` DO seam Q, `2b256cad` PM ratification). So nothing is queued for DA to ratify independently of this scope question — the hold blocks nothing currently in flight.

(Cross-refs in-body only: trigger = user-response `d1ceccf4`; concerns spec = `4eb18941`. Structured `references[]` omitted — write-time validation rejected the inbound ref to the just-written async-scoped `pmo-user-response` leaf; thread linkage via parent_id is sufficient.)
