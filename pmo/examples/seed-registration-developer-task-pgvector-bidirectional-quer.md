---
source: memsys (team: pmo)
id: b789e0d2-4dbc-4f52-84a2-ab8d30d782d9
type: note
version: 1
is_current: True
created_at: 2026-06-01T08:37:41.876177Z
updated_at: 2026-06-01T08:37:41.876177Z
tags: [current, demo-seed, infrastructure, pmo, pmo-identity-0a17e000-0000-4000-8000-000000000001, pmo-project-pmo-v1-build, pmo-registration, pmo-role-developer, pmo-seed, pmo-state-in-progress, pmo-working, project-manifest]
extracted_at: 2026-06-02
---

# [SEED · REGISTRATION] developer ↔ task pgvector-bidirectional-query

Area G demo seed — registration leaf (working memory). Binds the demo developer identity to the seeded task `453b141c` so `developer_assigned_tasks` (T7 self-discovery surface) returns a real row. Per Area A §6 registration tag schema. Threaded under manifest root `75e8523c`.

Payload:
- session_id: demo-dev-session-01
- cursor: "implementing both-direction similarity predicate; SQL draft in progress"
- registered_at: 2026-06-01T08:37Z
- work_item: task `453b141c` (pgvector bidirectional search query)
- identity: 0a17e000-0000-4000-8000-000000000001 (demo developer)
- state: in-progress
