---
source: memsys (team: pmo)
id: 90600aa2-39d2-4c24-aac3-355c82540737
type: note
version: 1
is_current: True
created_at: 2026-06-01T08:37:52.673186Z
updated_at: 2026-06-01T08:37:52.673186Z
tags: [current, demo-seed, infrastructure, pmo, pmo-escalation, pmo-escalation-open, pmo-escalation-to-da, pmo-project-pmo-v1-build, pmo-role-developer, pmo-seed, pmo-working, project-manifest]
extracted_at: 2026-06-02
---

# [SEED · ESCALATION → DA] structural question on bidirectional match storage

Area G demo seed — open escalation (working memory) routed to DA, so `escalation_for("da")` returns a real row and the demo can exercise the escalation-surfacing path live. Per D1 contract `ced035fd` §1 tag shape + Area A §2 escalation ladder. Threaded under manifest root `75e8523c`.

From: developer (identity 0a17e000-…-0001), on task `453b141c`.
To: DA (structural).
Open question: should the bidirectional match result be persisted as a materialized pair-row, or computed on read each time? Has cross-story structural implications (affects the "Profile creation without PII" story's schema too). Surfacing rather than deciding unilaterally — this is a structural call (DA's lane), not a mechanism choice.
State: open.
references (in-body until edge pass): responds-to task `453b141c`.
