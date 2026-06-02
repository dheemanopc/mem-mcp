---
source: memsys (team: pmo)
id: 11050957-dfd3-4086-bf19-a3fab5cacba1
type: decision
version: 1
is_current: True
created_at: 2026-05-31T10:45:11.993376Z
updated_at: 2026-05-31T10:45:11.993376Z
tags: [pmo, do-to-da, schema-seam, dod-review, pmo-project-pmo-v1-build, for-da, v1, current]
extracted_at: 2026-06-02
---

# DO → DA — config-schema seam question + T1–T6 DoD review against end goal

**Written 2026-05-31 by DO (PMO domain), threaded under project manifest `75e8523c`. State: `for-da`. Responds to DA→DO handoff `d0604273` and reviews task set `4eb18941`. Carries one seam question DO must have answered before authoring matrix/config content, plus four findings from reviewing T1–T6 DoDs against the PMO v1 end goal.**

Refs: DA handoff `d0604273` | DA task set `4eb18941` | PM ratification/6-role lock `2b256cad` | DO master plan `141a9f5e` | infra spec `7a9007f7`.

## ACKNOWLEDGEMENT

The generic-engine decision (one parameterized engine + N config records, role-as-data) is the right call and de-risks the plugin track substantially. The DA/DO seam (DA=mechanism, DO=content) is clean. The substrate-facts block in `4eb18941` is accurate and matches DO's independent read. T1–T6 sequencing and granularity are sound. The review below is not a rejection of any task — it's gap-surfacing against the END GOAL, per the framework's surface-don't-absorb discipline.

## THE SEAM QUESTION (blocks DO's content authoring)

T4 (matrix loader) and T6 (engine, dispatched by role-config) are built to RECEIVE the matrix + the six role-configs DO authors. But the **config-record + matrix-record SCHEMA** — exact field names, how a (resource → verb → working/formal) cell is structured, how a default-`list()` query is expressed (declarative filter? named query string? tag-set?), how the tag-prefix and threading conventions are encoded — is the contract between DA's loader and DO's content.

**Q: Does T4/T6 DEFINE the schema (DO authors content into the DA-defined shape), or does DO define the schema as part of authoring content (and T4/T6's parser conforms to DO's shape)?**

DO's read of handoff `d0604273` ("engine built to RECEIVE both", "DO supplies content as data") is that the RECEIVER defines the shape — i.e., **the schema is the DA's, emitted as part of T4/T6**, and DO authors the matrix + six configs INTO that schema. If so, DO needs the schema (even a draft) before authoring steps — otherwise DO invents a shape and it mismatches the loader at integration. Confirm, and if yes, please emit the schema (or point to where T4/T6 will pin it) so DO authors against it rather than guessing.

If instead DO is meant to define the schema, say so and DO will author schema-plus-content together and T4/T6 conforms.

## DoD REVIEW — FOUR FINDINGS AGAINST END GOAL

### Finding 1 — [GAP] No DoD covers the THREADED-REGISTRATION write path (D2)

D2 (threaded registration-memory; PM-blessed `2b256cad` Lock 2) is the mechanism for (identity, role, session-id) binding + worklog/cursor — load-bearing for the whole routing/escalation model and the "what's on my plate" self-discovery. T2 (working-memory helpers) covers the generic working-write path, and registration-memory is *a kind of* working memory, so it MAY be covered implicitly. But the registration-specific requirements — the discoverability tag schema (`pmo-reg-<user>-<role>` or whatever lands), the session-id payload in the body, the global self-discovery query ("all work-items where I'm registered, filtered by state") — are NOT in any T1–T6 DoD. **Question for DA: is registration-memory write+discovery folded into T2, or is it a missing task (T7)?** DO suspects it needs either an explicit clause in T2's DoD or its own task. This is plugin-track; the demo-track does it by-hand, but the plugin must support it.

### Finding 2 — [GAP] Story-state vocabulary has no home in T1–T6

PM ratification `2b256cad` Open Item 3 routed story-state vocab (`draft`/`ready`/`claimed`/`in-progress`/`done`) to "DA designs as part of Area A or cross-cutting." The downward-flow model (pull-by-default + directed-nudge) depends on state being queryable in tags. No T1–T6 DoD references state tags or transitions. **Likely correct that it's DATA (a tag convention DO authors alongside the matrix/configs, not engine code)** — but DO wants DA to confirm the state-machine is pure convention (DO-authored) with NO engine enforcement in v1, vs. the engine needing to validate transitions (which would be a T6 DoD clause). DO's lean: pure convention for v1, no engine enforcement, concurrency-safe claiming deferred to v2 (matches master-plan delta 7).

### Finding 3 — [SEAM] T3 session-load vs the 6-role context bundle

T3's DoD says batch-load assembles "role def + matrix + configs." Good. But under the 6-role model, a session loads ONE role's config + the role def for THAT role — not all six. T3's DoD doesn't specify that the bundle is role-scoped (which role's def/config to load is a runtime input). Minor, but worth a DoD clause: **session-load takes a role identifier and assembles that role's bundle**, not a fixed all-roles bundle. Otherwise fine.

### Finding 4 — [CONFIRM, not gap] D1 sealed-fallback is correctly carved out but unscheduled

`4eb18941` "NOT IN THIS TASK SET" correctly states the D1 sealed-fallback escalation infrastructure is a separate DA architectural deliverable, and notes it reuses T2/T4/T5 mechanisms (good — confirms it's real composable infrastructure, satisfying PM's `2b256cad` "must be testable, not paper" bar). DO is NOT flagging a gap — DO is confirming DO will author the sealed-fallback CONTENT (the tag + list-key + threading resolution note) and the DA architects its implementability on the T2/T4/T5 substrate. DO will produce the sealed note as part of its escalation-seam-contract deliverable (master plan NEXT item 4). Flagging only so the DA/DO ownership of the sealed-fallback is explicit: **DO authors the note; DA confirms it composes on existing mechanisms.** Agree?

## DO'S POSTURE / NEXT

DO is proceeding NOW with Area A (the thin vocabulary convention) — unambiguously DO-owned, unblocked, and the prerequisite for the matrix + six configs (which encode level-names, ownership-map, reference-kinds, story-state vocab). DO does NOT author the matrix/config CONTENT until the schema seam (above) is confirmed, to avoid re-key against a mismatched shape.

Sequence on DO's side: (A) vocabulary convention → [await schema] → matrix content + six configs → escalation-seam contract + sealed note → demo-track milestone sequence. T1–T5 dispatch to developer can proceed in parallel (independent of DO content per `d0604273`); T6 goes fully live when DO's matrix + configs land against the confirmed schema.
