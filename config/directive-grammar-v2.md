---
source: memsys (team: pmo)
id: ab0e7126-d0c6-481f-bfda-6f6b85fb0bd8
type: decision
version: 2
is_current: True
created_at: 2026-06-01T12:46:42.140577Z
updated_at: 2026-06-01T12:46:42.140577Z
tags: [pmo, directive-grammar, routing-contract, atpmo-marker, prompt-layer, pm-authored, for-pm, for-da, for-developer, pmo-project-pmo-v1-build, v1, current]
extracted_at: 2026-06-02
---

# PMO Directive Grammar v2 — `@pmo` clear-command marker (matches the installed Stop hook)

**Supersedes the v1 directive grammar `10852807`. The owner's installed Claude Code Stop hook uses a marker-on-last-line mechanism (grep the marker, isolate the command, send-keys with Enter). v2 aligns the grammar to that REALITY: agents emit a CLEAR COMMAND on the last line prefixed `@pmo`; the hook isolates `/resume <id>` (strip rule b) and types it with Enter. Threaded under manifest `75e8523c`.**

Refs: superseded grammar v1 `10852807` | the installed hook mechanism (owner-shared, `::word`→`/word` Stop hook) | routing config `08b19eee` (v1 reconciliation: auto-Enter — see note) | Area E v3 `3c5900e5` | role prompts v3 (PM `127793ef`, PA `897c696d`, DM `99c6c6cd`, DA `4e29970b`, Developer `da1c4c82`, Reviewer `6fff0238`).

## WHY v2 (what changed from v1 `10852807`)

v1 invented a verbose structured grammar (`@PMO NEXT resume=<id> role=<role> note="..."`) parsed into fields. The owner's ACTUAL installed hook is far simpler — a Stop hook that greps a marker line and sends the command. v2 matches the installed mechanism instead of imposing a new one:

- **Marker is `@pmo` (lowercase), last line of the response.** The hook greps the last `@pmo` line.
- **The agent emits a CLEAR, COMPLETE command after the marker** — for routing: `@pmo /resume <session-id>`. The prompt carries the burden of emitting the correct command (v1 posture: trust the prompt, validate later).
- **Strip rule (b):** the hook isolates exactly `/resume <id>` (id charset `[A-Za-z0-9_-]+`) and DROPS anything after the id. So even with auto-Enter, only a clean `/resume <id>` can execute — trailing text / injected `&&`/`;` is discarded. This is the v1 guardrail.
- **Auto-Enter (v1 assumption):** the hook sends `send-keys ... Enter` — it submits. (The owner mostly drives Claude from remote control, so auto-Enter is the practical v1 default. Registry-validation + fill-without-Enter is the v1.5 hardening; see config note below.)

## THE GRAMMAR (v1 — `@pmo` marker, last line)

The agent emits, as the LAST line of its response when a hand-off should happen:
```
@pmo /resume <session-id>
```
- `<session-id>` is the target role's Claude Code session id, which the emitting agent resolves from the session-registry (it read the registry on resume).
- The hook isolates `/resume <session-id>` and types it (+Enter). The human is in one window; this switches it to the target role's session.
- Emit NO `@pmo` line if still in dialogue / awaiting the human — that is normal and common.
- One directive per response, last line. The hook takes the LAST `@pmo` line.

### Non-routing intents (v1: surfaced in prose, not auto-typed)

ESCALATE, SPAWN, DONE, REVIEW_REQUEST from v1 grammar are NOT emitted as auto-typed `@pmo` commands in v2 (the hook only isolates `/resume`). Instead the agent states them in prose for the human to act on:
- **Escalation:** the agent writes the escalation as a working memory (tagged per D1 `ced035fd`) and tells the human in prose "escalating to DA — see <mem>"; the human routes (or resumes the DA themselves). Not auto-typed because escalations want human judgment (routing config `08b19eee` had these as `notify`).
- **Spawn:** the agent says in prose "we need a PA — spawn one" ; the human runs `pmo spawn`. Human-gated org change.
- **Done / review-request:** stated in prose; terminal or awaiting the human.

(v1.5 may extend the hook to handle more `@pmo` command shapes with validation; v1 keeps the hook to the one safe, high-frequency case: `/resume`.)

## INVARIANTS (v1)

1. **Emission is prompt discipline.** Each role prompt emits `@pmo /resume <id>` as the last line at hand-off; the id resolved from the registry. Works regardless of the hook (the human can read the line and act).
2. **Strip rule (b) is the guardrail.** Only `/resume <id>` (clean token) is ever typed; trailing content dropped. The hook does NOT (in v1) validate the id against the registry — the prompt is trusted to emit a real id; strip-b prevents arbitrary-command injection on the line.
3. **Auto-Enter (v1).** The hook submits. Reconciled into config `08b19eee` (see that memory's v2 note). Fill-without-Enter + registry-validation = v1.5.
4. **The directive is a pointer; substance lives in memsys.** Resume just switches sessions; the work/escalation content is in memsys memories the resumed agent reads on its on-resume block.

## WHAT THE PROMPTS EMBED (v2 emission block — replaces the v1 block in all six role prompts)

```
When a hand-off should happen, emit as the LAST line of your response:

  @pmo /resume <session-id>

where <session-id> is the target role's Claude Code session id (resolve it from the
session-registry you loaded on resume). This switches the single window to that role.

Emit NO @pmo line if you are still in dialogue or awaiting the human.
For an ESCALATION: write the escalation memory (tagged pmo-escalation/-to-<role>/-open)
and tell the human in prose who you're escalating to — do NOT emit an @pmo line.
For a SPAWN (need a new role/domain): say so in prose; the human runs `pmo spawn`.
Emit exactly one @pmo line, only for a /resume hand-off.
```

## STATE / NEXT
v2 supersedes `10852807`. The six role prompts' DIRECTIVE EMISSION blocks are updated to this `@pmo /resume <id>` shape (separate prompt edits). The installed hook (`pmo-stop-hook.sh`) implements this grammar + strip-rule-b + auto-Enter.
