---
source: memsys (team: pmo)
id: 453b141c-d6ec-4ef9-8b3a-e5420ca32bfa
type: decision
version: 1
is_current: True
created_at: 2026-06-01T08:37:30.409719Z
updated_at: 2026-06-01T08:37:30.409719Z
tags: [pmo, pmo-seed, pmo-work-item, pmo-level-task, pmo-role-developer, pmo-project-pmo-v1-build, pmo-state-in-progress, demo-seed, v1, current]
extracted_at: 2026-06-02
---

# [SEED · TASK] pgvector bidirectional search query

**Area G demo seed — task tier. Parent (derived-from) = story `e74edbd8` (Bidirectional match surfacing). State `in-progress`. This is the leaf the developer is "working"; a registration leaf (separate working memory) binds the developer identity to it so `developer_assigned_tasks` surfaces it.**

Refs (in-body until edge pass): parent story `e74edbd8` · Area A `940cfbae` · resolver spec `42022af0` (developer_assigned_tasks).

## WORK-ITEM
- **Level:** task (Area A §1)
- **Title:** pgvector bidirectional search query
- **Intent:** Implement the SQL that, for a candidate, evaluates both-direction fit via pgvector similarity + filter predicates in one round-trip.
- **State:** `in-progress`.
- **Parent:** story `e74edbd8` (derived-from edge pending final pass).
