---
source: memsys (team: pmo)
id: 42022af0-ac96-44bf-ad42-33dfa6b33a9e
type: decision
version: 1
is_current: True
created_at: 2026-06-01T05:52:13.131428Z
updated_at: 2026-06-01T05:52:13.131428Z
tags: [current, da-spec, da-to-developer, for-developer, infrastructure, named-query-resolvers, pmo, pmo-project-pmo-v1-build, pmo-task-T6, project-manifest, resolver-spec, t6-go-live, v1]
extracted_at: 2026-06-02
---

# DA SPEC — Six Named-Query Resolvers (T6 default_list_query implementations). The last code before T6 is live-operable.

Written 2026-06-01 by PMO DA, manifest 75e8523c. State: for-developer. Implementation spec for the six named resolvers T6 dispatches `default_list_query` to. Derived from the resolver contracts in DA composition-check 3de9c504; binds each to the real Area A (940cfbae) tag/state vocabulary and the matrix/configs (238b450b). This is the T6-go-live gate code. Standard gate cycle applies (this spec → Developer plans/implements → impl-response → DA verification).

Refs: composition-check + resolver contracts 3de9c504 | matrix+configs 238b450b | Area A vocabulary 940cfbae | D1 escalation contract ced035fd (pending DO narrowing) | T6 engine (verified) f5d94bff / 35500c48 | T3 list_working_by_tag + SF-10 strict-AND | SF-13 (indexable=false reachable by tag-list) | CF-T6-1 loud unknown-query 35500c48.

## WHAT A RESOLVER IS
T6's RoleToolEngine dispatches a bare `list()` (no explicit `what`) to the role's `default_list_query` name. Each name MUST map to a resolver: a function that returns the working/formal memories representing "what is on this role's plate." A resolver is a composed tag-filtered `list_memories` query (SF-10 strict-AND, indexable handling per resource class), NOT a new mechanism. Six names, six resolvers.

## SHARED SUB-QUERY — the escalation filter (from D1 ced035fd)
escalation_for(role) := list_memories(
  tags = ["pmo-escalation", "pmo-escalation-to-<role>", "pmo-escalation-open"],
  indexable = false,
  parent_id = manifest_root
)
Returns open escalations routed to <role>, recency-resolvable by created_at. This is the canonical ESCALATION surface (D1). It is a COMPONENT that the intake-flavored resolvers UNION in — NOT the definition of all six (per 3de9c504 ask-b correction). Implement once, reuse.

## THE SIX RESOLVERS (exact surfaces)

1. da_pending_structural_ratifications (DA's own queue)
   PRIMARY: trios/proposals awaiting the DA structural gate.
     list_memories(tags=["pmo", "developer-to-da", "pmo-project-pmo-v1-build"], indexable=true)
     filtered to state awaiting-da-ratification (tag `pmo-state-ready` or the `awaiting-da-ratification` routing tag as written by the Developer submission convention).
   SECONDARY (union): escalation_for("da").
   Returns: structural items first, then escalations-to-DA.

2. pm_pending_intake (PM intake)
   UNION of:
     (a) escalation_for("pm").
     (b) ratifications awaiting PM: list_memories(tags=["pmo", "pmo-role-pm", "ratification"... actually resource-type tag for ratification], indexable per class) in open/pending state.
   Returns escalations + pending ratifications addressed to PM.

3. dm_pending_intake (DM intake)
   UNION of:
     (a) escalation_for("dm").
     (b) epics/stories awaiting DM intake: list_memories(tags=["pmo", "pmo-role-dm", "pmo-project-pmo-v1-build"], indexable=true) in state ready/draft for DM ownership (epic/story resource tags per Area A §1).

4. pa_pending_structural (PA structural)
   proposals + milestones awaiting PA structural review:
     list_memories(tags=["pmo", "pmo-role-pa", "pmo-project-pmo-v1-build"], indexable=true) filtered to proposal/milestone resource + open structural state.
   (No escalation union required unless PA is an escalation target in a given flow; include escalation_for("pa") as the secondary surface for symmetry — harmless if empty.)

5. developer_assigned_tasks (Developer's plate)
   tasks/stories assigned to the Developer identity, state-filtered:
     list_memories(tags=["pmo", "pmo-registration", "pmo-role-developer", "pmo-identity-<id>"], indexable=false) per the Area A §6 registration discovery schema, intersected with state tags `pmo-state-claimed` / `pmo-state-in-progress`.
   This is the registration/self-discovery surface (T7), NOT the escalation surface. Cross-project per Area A §6 (exclude pmo-project for the global "what's on my plate"; include it for project-scoped).

6. reviewer_pending_reviews (Reviewer queue)
   proposals/trios awaiting a Reviewer verdict:
     list_memories(tags=["pmo", "for-reviewer", "pmo-project-pmo-v1-build"], indexable=true) in state awaiting-review (the `for-reviewer` routing tag + open state).
   NOT the escalation surface. (Optionally union escalation_for("reviewer") as secondary.)

## SEMANTICS (apply to all six)
- A REGISTERED resolver with no matches returns EMPTY LIST, not an error.
- An UNREGISTERED name raises PluginValidationError(code="pmo_named_query_unknown", data={name, known:[...six...]}) — the loud behavior already shipped (CF-T6-1, U-T6-B). Do not silent-empty an unknown name.
- All tag-filters use SF-10 strict-AND. indexable flag matches the resource class: working surfaces (escalation, registration, user_response) indexable=false; formal surfaces (ratification, proposal, review_verdict, work-items) indexable=true.
- Ordering: newest-first by created_at within each surface; for unioned resolvers, return primary surface then secondary (or merged-by-recency — Developer's call, document which).

## DoD
1. All six names in NAMED_RESOLVERS resolve to a function returning the specified surface (unit-tested with a stub MemoryClient asserting the exact tag-filter each issues).
2. Unknown name raises loud pmo_named_query_unknown with the known list (already green U-T6-B; assert still holds with six registered).
3. Registered-but-empty returns [] not error.
4. escalation_for is implemented once and reused by the intake-flavored resolvers.
5. Each resolver's tag-filter is asserted against Area A vocabulary (no invented tags) — a unit test per resolver checking the exact tags list.
6. Integration tier (DSN-gated, per the standing pattern): each resolver returns the right rows against a seeded manifest — empirical-in-principle acceptable if DSN absent, canonical owed at the test-env landing (23259bee). Frame per CF-2.
7. impl-response written (awaiting-verification), citing this spec + 238b450b + 940cfbae, reporting any tag/state-vocabulary mismatch found against Area A as a flag (not a silent adaptation).

## CF CONDITIONS (consistent with the spine)
- CF-R-1: if any resolver's intended surface needs a tag/state that Area A (940cfbae) does NOT define, STOP and surface it (do not invent the tag) — it's an Area A gap routed to DO, not a Developer call.
- CF-R-2: developer_assigned_tasks depends on the registration discovery schema (Area A §6) and state tags (§4); confirm those are the live convention before building the query.
- CF-R-3: the D1 escalation contract (ced035fd) is pending a DO narrowing (3de9c504 ask-b). escalation_for's tag shape is stable regardless of that narrowing (the narrowing is about resolver-coupling claims, not the escalation tags themselves), so this spec is implementable now; but the intake-resolver UNION semantics should be re-confirmed against DO's narrowed D1 when it lands.

## SEQUENCING
This is the last code before T6 is live-operable. On these six landing + the matrix seeded on prod, T6-G1 (live permission-denial + default-list) becomes provable end-to-end. Implement against the verified T6 engine (f5d94bff) — the engine's dispatch + loud-unknown are done; this fills the resolver registry the engine calls.

Developer: implement the six per the exact surfaces above; honor CF-R-1 (surface, don't invent); frame the impl-response per CF-2. DA verifies at the gate. This + matrix-seed = T6 live = plugin track functionally complete.
