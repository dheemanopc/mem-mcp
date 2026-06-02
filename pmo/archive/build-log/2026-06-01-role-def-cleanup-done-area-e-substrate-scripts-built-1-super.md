---
source: memsys (team: pmo)
id: 4273115b-0e8e-4d5b-a185-a8ccf20a311b
type: note
version: 1
is_current: True
created_at: 2026-06-01T09:46:25.047700Z
updated_at: 2026-06-01T09:46:25.859932Z
tags: [current, infrastructure, pmo, pmo-project-pmo-v1-build, pmo-role-do, pmo-working, pmo-working-note, project-manifest]
extracted_at: 2026-06-02
---

Role-def cleanup DONE + Area E substrate scripts BUILT. (1) Supersession lineage wired so v3 is canonical: PM 547b32ad→127793ef, Developer 82d88cc8→da1c4c82, Reviewer 3c6de6e0→6fff0238, Architect 9afdef9d→DA 4e29970b. PA 897c696d + DM 99c6c6cd are new (no predecessor). All six v3 role-defs live; old 4-role/per-role-API prompts retired. (2) Substrate scripts (Python-first, all tested): pmo_mux.py (tmux/psmux ADAPTER behind Mux base — owner uses psmux; one window/one pane, no splits; memsys-backed session-registry slug pmo-project-<slug>-session-registry with ~/.pmo local fallback; claude launch/resume primitives), pmo (operator CLI: up/spawn/where/resume/roles), pmo_hook.py (parses @PMO directive per grammar 10852807, applies routing config 08b19eee: NEXT→prefill /resume WITHOUT Enter, ESCALATE/SPAWN/REVIEW_REQUEST→notify, DONE→none; human always Enters; auto-Enter deferred), pmo-hook.sh (shell wrapper for CC hook runners). Syntax-checked + behaviorally tested (directive parse, prefill/notify decisions, no-directive silence, registry round-trip, resume-line gen). memsys wiring via PMO_MCP_CLI (canonical) + local fallback for dry-run. Files presented to owner. Remaining v1: wire PMO_MCP_CLI on demo machine + hook plumbing into Claude Code + dry-run; programmatic spawn is a later upgrade (v1 spawn = launch + register printed id).
