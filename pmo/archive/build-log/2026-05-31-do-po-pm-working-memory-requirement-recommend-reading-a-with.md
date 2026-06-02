---
source: memsys (team: pmo)
id: c38a096b-37d0-4f0f-9ccc-98c96bf8c7fc
type: decision
version: 1
is_current: True
created_at: 2026-05-31T13:53:27.609165Z
updated_at: 2026-05-31T13:53:27.609165Z
tags: [pmo, do-to-pm, to-po, working-memory-policy, t2-scope-question, pmo-project-pmo-v1-build, for-pm, v1, current]
extracted_at: 2026-06-02
---

# DO → PO/PM — working-memory requirement: recommend reading (a), with a convention refinement. (Responds to DA escalation `eec72021`.)

**Written 2026-05-31 by DO (PMO domain), threaded under project manifest `75e8523c`. State: `for-pm` / `to-po`. Responds to DA→DO escalation `eec72021`, which routed the owner's working-memory flag (verbatim `d1ceccf4`) to DO→PO per owner directive. DO's recommendation + reasoning; the (a)/(b) pick is PO/PM intent, so DO recommends and routes rather than closing. The DA holds the T2 structural gate pending this.**

Refs: DA escalation `eec72021` | owner verbatim `d1ceccf4` | DA T1 verification + SF-1..SF-5 `584614ac` | DA task set T2 spec `4eb18941` | DO master plan `141a9f5e` | architecture `121344a6`.

## THE QUESTION (as the DA framed it, correctly)

Owner flagged: working memories are "no more required, as we are able to retain session." Reads two ways:
- **(a) Narrow** — stop writing SELF-DIRECTED notes (a role's notes to its own future self), now redundant because a session retains its own history. T2 UNCHANGED.
- **(b) Broad** — roles stop persisting working state generally. T2 SHRINKS (possibly to just `pmo-user-response` capture); spine/DoDs need amendment.

## DO RECOMMENDATION: (a). And it is not a close call — here is why.

The decisive fact is in the owner's own phrasing: *"as we are able to retain session."* Session retention solves exactly ONE problem — **within-role, across-time continuity** (a single role resuming its own window without re-reading its own notes). It does nothing for the problem PMO's working-memory layer actually exists to solve: **across-ROLE coordination.**

These are different axes:
- **Session retention** = vertical (one role, through time). Per-session, per-role. The DA's window retains the DA's history; the Developer's retains the Developer's.
- **memsys working-memory** = horizontal (role → role). The Developer's session CANNOT see the DA's dialogue; the DA's cannot see DO's. The ONLY thing that bridges them is content persisted in memsys under this manifest.

Concrete proof in this very thread: the memo you are reading was written by DO so the PO can act on it. The DA's escalation `eec72021` was written so DO could act on it. The Reviewer verdicts `7f290667`/`21a5a539` were written so the Developer could act on them. The DA verification `584614ac` locked SF-1..SF-5 so the DEVELOPER's future T2–T7 windows inherit them. None of these is a self-note; every one is a cross-role bridge that session retention would NOT carry. Remove them and the framework loses its coordination substrate — and the demo's "process must not fail" target (`d7a6c240`) fails immediately, because the escalation ladder has nothing to escalate THROUGH.

So under any reading where the framework still works, the cross-role artifacts MUST persist. That rules out (b) as stated. What remains is (a).

## WHAT (a) ACTUALLY CHANGES — a real refinement, not a no-op

The owner's flag is NOT empty — it points at a genuine inefficiency, just not in T2's scope. A lot of what's been written this session ARE self-directed working notes that session retention now makes redundant:
- Developer session-start notes (`b7ee2c79`, `473cfa93`) — "here's what I loaded, here's my posture."
- The DA session checkpoint (`e4e61a71`) — explicitly a "resume-context for my own next window" note.
- DO's own working notes (`63f6b956`, `05e1d80c`, `f35579f3`, `3aae9512`) — status/cursor for DO's future self.

If session retention is reliable, these can be trimmed. THAT is the actionable content of the owner's flag. But it is a **convention refinement to Area A (the vocabulary/working-memory convention DO owns), NOT a change to T2's mechanism.** The distinction:

- **Self-directed working note** (write for my own future self): now OPTIONAL. Session retention covers it. Area A convention will say: prefer session retention for self-continuity; write a self-note only when you need it durable across a session LOSS (crash, context-window overflow, machine change) — retention is not a durable store.
- **Cross-role working memory** (write so ANOTHER role can pick it up): STILL REQUIRED. This is the bridge. T2 builds exactly this path.
- **`pmo-user-response` capture** (verbatim owner words): STILL REQUIRED. Owner kept it required in this very session (the capture `d1ceccf4` IS one). It is the audit trail of intent.

## NET FOR T2 (the DA's held gate)

**T2 is UNCHANGED under (a).** Its DoD targets the cross-role bridge (tagged leaves another role retrieves) + `pmo-user-response` capture — both of which survive. The DA can RELEASE the T2 structural gate the moment PO/PM confirms (a). The Developer's pending T2 intent question (`e153cdb8`, user-response capture as dedicated function vs tag convention) is also unaffected — answer it independently (DO will, separately; lean: dedicated function, the verbatim contract is a framework concern).

**One caveat worth a durability note even under (a):** session retention is NOT a durable store — it's convenience, not persistence. If a session is lost (crash, context overflow, deliberate reset) before its work is bridged into memsys, retained-but-unwritten state is GONE. So the Area A convention should say: a role may rely on retention for in-flight self-continuity, but MUST persist to memsys any state that another role — or a post-loss resume — will need. "Retain for self, persist for others (and for durability)." This keeps (a) from quietly reintroducing the data-loss failure mode that working-memory persistence was guarding against.

## WHAT DO ASKS OF PO/PM

Confirm the pick. DO recommends:
- **(a) — T2 unchanged, DA releases the gate, Developer proceeds.**
- DO folds the refinement into Area A: self-notes optional (retention covers self-continuity), cross-role + user-response persistence required, "retain for self / persist for others + durability" as the rule.

If PO/PM actually intends (b) (a broader "stop persisting working state" that DO is not seeing the rationale for), say so explicitly with the one-line statement of which working-memory classes survive, and DO + DA will re-scope T2's DoD against it. But DO's strong read is (a): the cross-role bridge is non-negotiable for the framework to function, and the real win the owner is pointing at is trimming self-notes, which is an Area A convention change, not a T2 mechanism change.

## DO POSTURE

DO has NOT changed Area A or any T2-facing artifact yet — holding for PO/PM's (a)/(b) confirmation, since authoring the convention refinement presupposes the answer. On (a)-confirm, DO writes the Area A working-memory convention (it was already the next deliverable) WITH this refinement baked in, and notifies the DA to release the T2 gate.
