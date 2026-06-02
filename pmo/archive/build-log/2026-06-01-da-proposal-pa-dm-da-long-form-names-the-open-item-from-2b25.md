---
source: memsys (team: pmo)
id: e58b5513-7b6a-472b-881a-5ce748c185c6
type: decision
version: 1
is_current: True
created_at: 2026-06-01T05:54:05.781795Z
updated_at: 2026-06-01T05:54:05.781795Z
tags: [current, da-proposal, da-to-do, da-to-pm, for-do, for-pm, infrastructure, pmo, pmo-project-pmo-v1-build, project-manifest, role-naming, v1]
extracted_at: 2026-06-02
---

# DA PROPOSAL — PA / DM / DA long-form names (the open item from 2b256cad). For owner/PM ratification.

Written 2026-06-01 by PMO DA, manifest 75e8523c. State: for-pm / for-do. Proposes the long-form names for the three roles whose codes are locked but whose names 2b256cad left open ("DA proposes during dialogue; owner ratifies"). This blocks nothing and was the one DA-lane item actionable without waiting on a gate; picking it up while the resolver spec (42022af0) goes to the Developer. CODES (pa/dm/da) are canonical and UNCHANGED regardless of the names chosen (Area A 940cfbae §2) — this is purely the human-readable label.

Refs: PM 6-role lock + open-names note 2b256cad | Area A §2 ownership map 940cfbae | locked architecture 121344a6 | DO master plan 141a9f5e.

## THE CONSTRAINT (from 2b256cad + Area A §2)
The concept + tier are LOCKED; only the name is open. Recursively, each tier pairs an intent-owner with a structure-owner:
- PM = project-tier INTENT (locked name: Project Manager)
- PA = project-tier STRUCTURE
- DM = milestone/epic-tier INTENT
- DA = milestone/epic-tier STRUCTURE
- Developer = task MECHANISM (locked)
- Reviewer = task JUDGMENT (locked)
The master plan used "Domain/Milestone" with a slash for DM/DA because the middle tier wasn't cleanly named. This proposal resolves that.

## THE NAMING PROBLEM
Two things must be true: (1) the intent/structure pairing should be legible in the names (PM:PA reads as Manager:Architect — the same pair should repeat at the middle tier), and (2) the middle tier needs ONE word, not "Domain/Milestone". The tier's unit is the MILESTONE (and its epics) — "Domain" was a carryover. So the middle tier word should be "Milestone" (what the tier owns) — but "Milestone Manager/Architect" is clunky and collides conceptually with PM. Alternative tier words considered: Domain, Delivery, Program.

## PROPOSAL (recommended) — keep the Manager/Architect pairing, name the tier "Delivery"
- PA = **Project Architect** (already the working name; keep — reads cleanly against Project Manager).
- DM = **Delivery Manager** (milestone/epic intent — which epics, what order, milestone success).
- DA = **Delivery Architect** (milestone/epic structure — how stories compose, cross-epic seams).

Rationale: "Delivery" names what the middle tier actually produces (shippable milestone increments) without colliding with "Project" (tier above) or "task" (tier below); it's a real industry term (delivery manager/lead) so it reads naturally; and Manager/Architect repeats the PM/PA intent/structure pairing exactly one tier down, making the recursive structure self-evident from the names alone. The codes stay pa/dm/da — "Delivery" starts with D, so DM/DA codes even keep a mnemonic link.

## ALTERNATIVES (for owner's choice)
- ALT-1 "Milestone Manager / Milestone Architect" — most literal (the tier's unit is the milestone), but clunky and the "MM/MA" mnemonic conflicts with the locked dm/da codes.
- ALT-2 "Domain Manager / Domain Architect" — keeps the dm/da mnemonic and the master plan's original "Domain" word; downside is "Domain" is vaguer than "Delivery" about what the tier owns (a domain isn't inherently milestone-shaped).
- ALT-3 "Program Manager / Program Architect" — "Program" is a recognized tier between project and task; downside is "Program Manager" is a loaded industry title that may imply a different scope than this tier holds.

DA recommendation: the recommended "Delivery" option, with ALT-2 "Domain" as the fallback if owner prefers preserving the dm/da-mnemonic literalism over the tier-clarity of "Delivery". Either is fine structurally; it's a naming-taste call, which is the owner's.

## SCOPE / IMPACT
Pure label change. NO code, tag, matrix, config, or slug impact — all of those key off the locked codes (pa/dm/da), never the long-form name (Area A §2, enforced). So this can be ratified at any time with zero rework; it only affects human-readable role docs/prompts. Non-blocking, non-urgent.

## ASK
Owner/PM: pick the recommended "Delivery Manager / Delivery Architect" (+ Project Architect), or one of the alternatives. On ratification, DO folds the chosen names into Area A §2 as the canonical long-form labels (codes unchanged). Until ratified, the codes remain the only canonical identifier, which is already the operating rule — so nothing waits on this.
