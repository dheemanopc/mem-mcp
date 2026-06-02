---
source: memsys (team: pmo)
id: c4a7c20f-b349-4dc5-87d4-03b535eff236
type: note
version: 1
is_current: True
created_at: 2026-06-01T05:27:01.269987Z
updated_at: 2026-06-01T05:27:01.975603Z
tags: [current, infrastructure, pmo, pmo-project-pmo-v1-build, pmo-role-do, pmo-working, pmo-working-note, project-manifest]
extracted_at: 2026-06-02
---

DO authored the two critical-path content artifacts. (1) Area A thin vocabulary convention → 940cfbae (slug pmo-area-a-vocabulary-convention-v1): level-names, 6-role ownership-map with stable codes, reference-kind registry, story-state vocab (pure convention/no enforcement), working-memory discipline (PM ruling ba6d113a + durability caveat), registration tag schema (per T7 c9095015), standard working tag set, project-slug convention. (2) Matrix + six role-configs → 238b450b (slug pmo-permission-matrix-v1) against schema 7dcbb2c8: validated against BOTH load-time invariants before write (config-verbs ⊆ matrix-cell: PASS; default_list_query ∈ registry: PASS; roles/matrix/configs key-aligned; classes valid). delete granted to no role v1 (audit-trail intact). review_verdict class is per-role (formal for Reviewer, working for DA/Developer readers). THE COUPLING POINT: six named-query resolvers T6 must implement — pm_pending_intake, pa_pending_structural, dm_pending_intake, da_pending_structural_ratifications, developer_assigned_tasks, reviewer_pending_reviews. This is the T6-go-live gate: DA confirms matrix→configs→engine composition + assigns resolver implementation to Developer. CONSEQUENCE: T6 was inert (pmo_matrix_not_loaded); with this content it goes live once the six resolvers exist. DO remaining: D1 sealed-fallback note (→DA composition-check) + answer e153cdb8 (capture-interface; lean dedicated-function). Then next track: tmux substrate (E) + B/C/D demo conventions + G readiness.
