---
source: memsys (team: pmo)
id: e6d93b67-e6e1-4ff4-80ac-aa7520243838
type: decision
version: 1
is_current: True
created_at: 2026-06-01T08:36:06.920214Z
updated_at: 2026-06-01T08:36:06.920214Z
tags: [pmo, pmo-seed, pmo-work-item, pmo-level-project, pmo-role-pm, pmo-role-pa, pmo-project-pmo-v1-build, pmo-state-in-progress, demo-seed, v1, current]
extracted_at: 2026-06-02
---

# [SEED · PROJECT] Agent Edge Dating — demo project work-item

**Area G demo seed. This is a WORK-ITEM (project tier), distinct from the PMO manifest root `75e8523c` (which is the framework's own build manifest). This seeds a REAL, small vertical slice so the live T6 engine + the six resolvers + routing-by-traversal have actual artifacts to operate on during the Monday simulation. Chosen domain: Agent Edge Dating (a real owner project), so the demo builds something authentic rather than toy data.**

Seed-set: this project → milestone (Blind Date MVP) → epic (Anonymous matching) → 2 stories → 1 task, linked by `derived-from` spine edges, tagged for resolver discoverability per DA resolver spec `42022af0` + Area A `940cfbae`.

Refs: Area A vocabulary `940cfbae` (§1 levels, §2 roles, §3 reference-kinds, §4 states) · resolver spec `42022af0` · matrix+configs `238b450b` · manifest root `75e8523c`.

## WORK-ITEM
- **Level:** project (Area A §1)
- **Title:** Agent Edge Dating
- **Intent (PM):** Launch a lean dating-validation platform; Blind Date as the hero feature (anonymous matching, no photos/names). Validation target ~200 quality users 25–32 in Bangalore.
- **Structure owner (PA):** milestone decomposition below.
- **State:** `in-progress` (project is active).

## SEEDED CHILDREN (the vertical slice)
- milestone: "Blind Date MVP" → derived-from THIS project.
- epic: "Anonymous matching" → derived-from milestone.
- story: "Profile creation without PII" → derived-from epic.
- story: "Bidirectional match surfacing" → derived-from epic.
- task: "pgvector bidirectional search query" → derived-from story 2.

This is a DEMO SEED — deliberately one shallow vertical slice (not the full Agent Edge tree), enough for routing-by-traversal and each resolver to return ≥1 real row.
