---
source: memsys (team: pmo)
id: 5ae2759b-795d-41b9-b730-6b1eeeabb83f
type: fact
version: 1
is_current: True
created_at: 2026-06-02T02:44:45.619237Z
updated_at: 2026-06-02T02:44:45.619237Z
tags: [pmo, session-registry, current]
extracted_at: 2026-06-02
---

# PMO SESSION REGISTRY v1

Active role-sessions for the PMO 6-role ladder. Role-name → session-id. Used by directive emission (`@PMO NEXT resume=<id> role=<role>`) to resolve the target session.

| role      | session-id                           | registered_at (UTC) |
|-----------|--------------------------------------|---------------------|
| pm        | b4096323-bbec-422c-9c13-c7e3ed8b94a0 | 2026-06-02T02:18:32Z |
| pa        | d42def77-8b06-4fd3-b10f-a028e930748c | 2026-06-02T02:18:36Z |
| dm        | 07a48b48-8750-4f3f-a045-454b874098d5 | 2026-06-02T02:18:41Z |
| da        | b05a7e18-58e7-4fab-b528-2f1527235349 | 2026-06-02T02:18:45Z |
| developer | 4d6b8d84-cb3c-4771-b44a-f7e51ed65cc4 | 2026-06-02T02:18:51Z |

NOTE: `reviewer` role-session not yet registered (6-role ladder; SPAWN when needed). Update this registry via `memory_supersede` when sessions are added/replaced.
