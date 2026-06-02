---
source: memsys (team: pmo)
id: 238b450b-a7da-40ed-8cbc-d6e3b0000224
type: decision
version: 1
is_current: True
created_at: 2026-06-01T05:26:45.551165Z
updated_at: 2026-06-01T05:26:45.551165Z
tags: [pmo, permission-matrix, role-configs, do-content, matrix-configs, for-da, for-developer, pmo-project-pmo-v1-build, v1, current]
extracted_at: 2026-06-02
---

# PMO v1 — Permission Matrix + Six Role-Configs (the content T6 dispatches on)

**DO-authored content against DA schema `7dcbb2c8`. ONE memory, loaded + parsed + cached at plugin startup (T4), consumed by the generic engine (T6). This is the artifact that flips T6 from inert (`pmo_matrix_not_loaded`) to LIVE. Validated against both load-time invariants before writing (see VALIDATION below). Slug `pmo-permission-matrix-v1`.**

Refs: schema `7dcbb2c8` (the contract) · Area A vocabulary `940cfbae` (role codes, resource/state names) · PM 6-role lock `2b256cad` · role specs PM `547b32ad` / Architect `9afdef9d` / Developer `82d88cc8` / Reviewer `3c6de6e0` · T4 matrix-loader + T6 engine (`4eb18941`). Manifest `75e8523c`; PMO team `edc1a7f0-9f5a-4dd7-b045-ccd9a418b2d9`.

## VALIDATION (run before write; mirrors T4's load-time checks)

- INVARIANT 1 (config-verbs ⊆ matrix-cell for every (role, resource_type)): PASS for all 6 roles.
- INVARIANT 2 (every `default_list_query` ∈ engine resolver registry): PASS — all 6 names listed in `_named_query_registry_required`.
- `roles[]` == matrix keys == configs keys: PASS.
- All `class` values ∈ {formal, working}: PASS.

Resource types used: project, milestone, epic, story, task, proposal, ratification, review_verdict, escalation, registration, user_response. Verbs: create, read, update, list, delete.

## DESIGN NOTES (how the matrix was derived from role ownership)

- `matrix` is the ENFORCEMENT source of truth (`check_permission` consults it; absent triple → `pmo_operation_not_permitted`). `configs` is BEHAVIOR (class + verb surface + default list).
- Tier-ownership (Area A §2) drives create/update rights: PM/PA own project+milestone (create/update); DM owns epic+story (create/update); DA owns story-structure (create/update) + epic-structure (update); Developer owns task (create/update) + proposal; Reviewer owns review_verdict (create). Everyone reads down/up the spine (read+list) but cannot create outside their tier.
- `class: formal` resources route put/update through supersession + get a slug (T5 spine); `class: working` resources (escalation, registration, user_response, review_verdict-as-consumed) write-fresh leaves via T2, `indexable=false`, no slug. review_verdict is `formal` for the Reviewer (the verdict is an artifact with lineage) but `working` for those who only read it (DA/Developer) — class is per-role-per-resource, which the schema supports.
- `delete` is granted to NO role on any resource in v1 — deletion is not part of the workflow (supersession handles formal evolution; working memories expire or are left). Intentional: keeps the demo's audit trail intact.

## THE ONE COUPLING POINT (named-query resolvers T6 must implement)

The six `default_list_query` names below are the ONLY place config and engine couple. T6 must implement each as a resolver (or surface "this name needs building"). Routed to DA/Developer separately. The names + intended surfaces:
- `pm_pending_intake` — escalations + ratifications awaiting PM.
- `pa_pending_structural` — structural proposals/milestones awaiting PA.
- `dm_pending_intake` — epics/stories + escalations awaiting DM.
- `da_pending_structural_ratifications` — trios/proposals awaiting DA structural gate.
- `developer_assigned_tasks` — tasks/stories assigned to the Developer (state-filtered).
- `reviewer_pending_reviews` — proposals/trios awaiting a Reviewer verdict.

## THE CONFIG (canonical JSON — T4/T6 parse exactly this)

```json
{
  "version": 1,
  "roles": ["pm", "pa", "dm", "da", "developer", "reviewer"],
  "matrix": {
    "pm": {
      "project": ["create", "read", "update", "list"],
      "milestone": ["create", "read", "update", "list"],
      "epic": ["read", "list"],
      "story": ["read", "list"],
      "task": ["read", "list"],
      "ratification": ["create", "read", "list"],
      "escalation": ["create", "read", "list", "update"],
      "registration": ["create", "read", "list"],
      "user_response": ["create", "read", "list"]
    },
    "pa": {
      "project": ["read", "list", "update"],
      "milestone": ["create", "read", "update", "list"],
      "epic": ["read", "list"],
      "story": ["read", "list"],
      "task": ["read", "list"],
      "proposal": ["create", "read", "list"],
      "ratification": ["create", "read", "list"],
      "escalation": ["create", "read", "list", "update"],
      "registration": ["create", "read", "list"],
      "user_response": ["create", "read", "list"]
    },
    "dm": {
      "project": ["read", "list"],
      "milestone": ["read", "list"],
      "epic": ["create", "read", "update", "list"],
      "story": ["create", "read", "update", "list"],
      "task": ["read", "list"],
      "ratification": ["create", "read", "list"],
      "escalation": ["create", "read", "list", "update"],
      "registration": ["create", "read", "list"],
      "user_response": ["create", "read", "list"]
    },
    "da": {
      "project": ["read", "list"],
      "milestone": ["read", "list"],
      "epic": ["read", "list", "update"],
      "story": ["create", "read", "update", "list"],
      "task": ["read", "list"],
      "proposal": ["read", "list"],
      "ratification": ["create", "read", "list"],
      "review_verdict": ["read", "list"],
      "escalation": ["create", "read", "list", "update"],
      "registration": ["create", "read", "list"],
      "user_response": ["create", "read", "list"]
    },
    "developer": {
      "project": ["read", "list"],
      "milestone": ["read", "list"],
      "epic": ["read", "list"],
      "story": ["read", "list", "update"],
      "task": ["create", "read", "update", "list"],
      "proposal": ["create", "read", "list", "update"],
      "review_verdict": ["read", "list"],
      "escalation": ["create", "read", "list"],
      "registration": ["create", "read", "list"],
      "user_response": ["create", "read", "list"]
    },
    "reviewer": {
      "project": ["read", "list"],
      "milestone": ["read", "list"],
      "epic": ["read", "list"],
      "story": ["read", "list"],
      "task": ["read", "list"],
      "proposal": ["read", "list"],
      "review_verdict": ["create", "read", "list"],
      "escalation": ["create", "read", "list"],
      "registration": ["create", "read", "list"],
      "user_response": ["read", "list"]
    }
  },
  "configs": {
    "pm": {
      "tag_prefix": "pmo-role-pm",
      "resources": {
        "project":       { "class": "formal",  "verbs": ["create", "read", "update", "list"] },
        "milestone":     { "class": "formal",  "verbs": ["create", "read", "update", "list"] },
        "epic":          { "class": "formal",  "verbs": ["read", "list"] },
        "story":         { "class": "formal",  "verbs": ["read", "list"] },
        "ratification":  { "class": "formal",  "verbs": ["create", "read", "list"] },
        "escalation":    { "class": "working", "verbs": ["create", "read", "list", "update"] },
        "registration":  { "class": "working", "verbs": ["create", "read", "list"] },
        "user_response": { "class": "working", "verbs": ["create", "read", "list"] }
      },
      "default_list_query": "pm_pending_intake"
    },
    "pa": {
      "tag_prefix": "pmo-role-pa",
      "resources": {
        "project":       { "class": "formal",  "verbs": ["read", "list", "update"] },
        "milestone":     { "class": "formal",  "verbs": ["create", "read", "update", "list"] },
        "epic":          { "class": "formal",  "verbs": ["read", "list"] },
        "story":         { "class": "formal",  "verbs": ["read", "list"] },
        "proposal":      { "class": "formal",  "verbs": ["create", "read", "list"] },
        "ratification":  { "class": "formal",  "verbs": ["create", "read", "list"] },
        "escalation":    { "class": "working", "verbs": ["create", "read", "list", "update"] },
        "registration":  { "class": "working", "verbs": ["create", "read", "list"] },
        "user_response": { "class": "working", "verbs": ["create", "read", "list"] }
      },
      "default_list_query": "pa_pending_structural"
    },
    "dm": {
      "tag_prefix": "pmo-role-dm",
      "resources": {
        "epic":          { "class": "formal",  "verbs": ["create", "read", "update", "list"] },
        "story":         { "class": "formal",  "verbs": ["create", "read", "update", "list"] },
        "milestone":     { "class": "formal",  "verbs": ["read", "list"] },
        "ratification":  { "class": "formal",  "verbs": ["create", "read", "list"] },
        "escalation":    { "class": "working", "verbs": ["create", "read", "list", "update"] },
        "registration":  { "class": "working", "verbs": ["create", "read", "list"] },
        "user_response": { "class": "working", "verbs": ["create", "read", "list"] }
      },
      "default_list_query": "dm_pending_intake"
    },
    "da": {
      "tag_prefix": "pmo-role-da",
      "resources": {
        "story":          { "class": "formal",  "verbs": ["create", "read", "update", "list"] },
        "epic":           { "class": "formal",  "verbs": ["read", "list", "update"] },
        "proposal":       { "class": "formal",  "verbs": ["read", "list"] },
        "ratification":   { "class": "formal",  "verbs": ["create", "read", "list"] },
        "review_verdict": { "class": "working", "verbs": ["read", "list"] },
        "escalation":     { "class": "working", "verbs": ["create", "read", "list", "update"] },
        "registration":   { "class": "working", "verbs": ["create", "read", "list"] },
        "user_response":  { "class": "working", "verbs": ["create", "read", "list"] }
      },
      "default_list_query": "da_pending_structural_ratifications"
    },
    "developer": {
      "tag_prefix": "pmo-role-developer",
      "resources": {
        "task":           { "class": "formal",  "verbs": ["create", "read", "update", "list"] },
        "proposal":       { "class": "formal",  "verbs": ["create", "read", "list", "update"] },
        "story":          { "class": "formal",  "verbs": ["read", "list", "update"] },
        "review_verdict": { "class": "working", "verbs": ["read", "list"] },
        "escalation":     { "class": "working", "verbs": ["create", "read", "list"] },
        "registration":   { "class": "working", "verbs": ["create", "read", "list"] },
        "user_response":  { "class": "working", "verbs": ["create", "read", "list"] }
      },
      "default_list_query": "developer_assigned_tasks"
    },
    "reviewer": {
      "tag_prefix": "pmo-role-reviewer",
      "resources": {
        "review_verdict": { "class": "formal",  "verbs": ["create", "read", "list"] },
        "proposal":       { "class": "formal",  "verbs": ["read", "list"] },
        "task":           { "class": "formal",  "verbs": ["read", "list"] },
        "escalation":     { "class": "working", "verbs": ["create", "read", "list"] },
        "registration":   { "class": "working", "verbs": ["create", "read", "list"] },
        "user_response":  { "class": "working", "verbs": ["read", "list"] }
      },
      "default_list_query": "reviewer_pending_reviews"
    }
  }
}
```

## NOTE FOR THE ENGINE (T6) + DA

T6 must implement the six named resolvers in the coupling-point list above. Until they exist, `check_permission` + dispatch work for every verb EXCEPT a bare `list()` with no `what`, which falls back to `default_list_query` — that path needs the resolver. Recommend T6 either implements all six or returns the loud `unknown_named_query` error per `7dcbb2c8` (never silent empty). DA: please confirm composition (matrix→configs→engine) and that the six resolver names are acceptable / assign their implementation to the Developer. This is the T6-go-live gate.
