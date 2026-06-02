---
source: memsys (team: pmo)
id: 93e0da61-bfca-4c89-bc9e-de6653a84012
type: note
version: 1
is_current: True
created_at: 2026-06-01T08:42:32.197242Z
updated_at: 2026-06-01T08:42:32.793399Z
tags: [current, infrastructure, pmo, pmo-project-pmo-v1-build, pmo-role-do, pmo-working, pmo-working-note, project-manifest]
extracted_at: 2026-06-02
---

Area E (tmux substrate, v1-mandatory) PLANNED → f679c7db (slug pmo-area-e-tmux-substrate-plan) + 5 runnable scripts + README produced as files. Model: session=project (pmo-<slug>), pane=role (6 codes), session-registry memory (slug pmo-project-<slug>-session-registry, decision, evolves by supersede) maps role->{claude_session_id,tmux_target}. CORRECTED resume primitive: /resume <session-id> (slash into live REPL, cross-session handoff) NOT claude --resume (that's cold-restart only) — grammar fix vs c6c75cf9 + 0d93f919 per owner clarification in 141a9f5e; this memo authoritative on that point. On-resume prefix = load role-def-by-slug + matrix/configs + Area A + thread-get manifest + run role default-list ("what's on my plate"). Scripts: pmo-session-up (tiled titled panes), pmo-register (emits registry-row supersede), pmo-where (read registry table), pmo-goto (jump to pane), pmo-resume-hint (print /resume line). Registry memory is canonical; scripts are ergonomics over it (memsys boundary explicit, no second source of truth). v1.5 deferred: auto-routing/hooks/send-keys/idle-detect. Demo choreography wires E onto the Area G seed bf7b3379 (seeded escalation 90600aa2 surfaces on DA pane resume). Reference defect + R6-G1 don't block E (registry/resolvers are tag/slug-based). DO read: E is demo-track scripts not a gate-cycled plugin task (per 141a9f5e two-track). Remaining v1: B/C/D demo conventions (thin) + G readiness dry-run on demo machine. Open for owner review.
