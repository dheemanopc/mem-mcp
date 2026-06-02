---
source: memsys (team: pmo)
id: 714c71a6-0e0b-4658-a1c0-4dfeda6d1ffa
type: decision
version: 2
is_current: True
created_at: 2026-06-01T12:47:05.587532Z
updated_at: 2026-06-01T12:47:05.587532Z
tags: [pmo, area-e, routing-config, auto-enter, do-content, demo-track, for-pm, for-da, for-developer, pmo-project-pmo-v1-build, v1, current]
extracted_at: 2026-06-02
---

# PMO Area E — routing config v2 (auto-Enter reconciliation)

**Supersedes routing config v1 `08b19eee`. v1 said "human always presses Enter; auto-Enter deferred." The owner's INSTALLED Stop hook auto-Enters (`send-keys ... Enter`), and the owner confirmed auto-Enter is the v1 assumption (they mostly drive Claude from remote control, where auto-Enter is the practical default). v2 reconciles the config to deployed reality. Threaded under manifest `75e8523c`.**

Refs: superseded config v1 `08b19eee` | directive grammar v2 `ab0e7126` | installed hook `pmo-stop-hook.sh` | Area E v3 `3c5900e5`.

## THE RECONCILIATION (what changed)

v1 framed routing as `prefill` (fill, human Enters) vs `notify` (passive). The installed hook is simpler and AUTO-ENTERS the one command it handles (`/resume`). So v2:

- **v1 reality = AUTO-ENTER for the `/resume` hand-off.** The Stop hook isolates `/resume <id>` (strip rule b) and types it WITH Enter. No human keystroke for a routing hand-off. The owner accepted this for v1 (remote-control usage; the strip-rule-b guardrail means only a clean `/resume <id>` can execute — no arbitrary command injection on the line).
- **The guardrail is STRIP-RULE-B, not human-Enter.** v1's safety came from "human presses Enter." v2's v1 safety comes from the hook isolating exactly `/resume <id>` and dropping any trailing/injected text. That is what makes auto-Enter acceptable: the worst an agent can do is auto-resume a (possibly wrong but well-formed) session id — visible in the window — never execute an arbitrary command.
- **Only `/resume` is auto-handled.** ESCALATE / SPAWN / DONE / REVIEW_REQUEST are NOT auto-typed (the hook only isolates `/resume`). They are surfaced in prose for the human to act on (grammar v2 `ab0e7126`). So the "notify" class from v1 survives as "stated in prose, human acts" — just not via the hook.

## v1 BEHAVIOR (effective)

- `@pmo /resume <id>` (NEXT-style hand-off) → hook isolates + auto-Enters. The high-frequency, low-risk case.
- Escalation / spawn / done / review-request → agent states in prose + (for escalation) writes the escalation memory; human routes. No auto-type.

## DEFERRED TO v1.5 (the hardening, unchanged in intent from v1)

- **Registry-validation of the resume id** (the hook checks the id is a registered session before typing) — deferred; v1 trusts the prompt to emit a real id, with strip-rule-b as the injection guard. (The Python `pmo_hook.py` HAS this validation; the installed shell Stop hook does not — v1.5 either ports validation into the shell via a Python helper, or switches to the Python hook.)
- **Fill-without-Enter + per-directive-type policy** (the original v1 `prefill`/`notify` map) — deferred. If the owner later wants "read before resuming" on some paths, the per-path policy from `08b19eee` returns, with auto-Enter as a per-path opt-IN rather than the global default.
- **More `@pmo` command shapes handled by the hook** (beyond `/resume`), each with validation.

## STATE
v2 supersedes `08b19eee`. Reflects the installed hook. The per-directive-type policy table in v1 is retained as the v1.5 target, not the v1 behavior. v1 behavior = auto-Enter `/resume` via the Stop hook, strip-rule-b as guardrail, everything else surfaced in prose.
