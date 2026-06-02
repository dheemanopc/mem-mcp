---
source: memsys (team: pmo)
id: c4579996-4681-4143-9fdd-0e277fffe08c
type: fact
version: 1
is_current: True
created_at: 2026-06-02T02:46:45.255884Z
updated_at: 2026-06-02T02:46:46.267953Z
tags: [pmo, directive-grammar, current, correction]
extracted_at: 2026-06-02
---

# PMO DIRECTIVE GRAMMAR — short form (parser truth)

The orchestrator's Python parser only recognizes the SHORT directive form. Emit just:

```
@PMO /resume <session-id>
```

Do NOT emit the long form (`@PMO NEXT resume=<id> role=<role> note="..."`) — the parser ignores it.

**Why:** Confirmed by owner 2026-06-02 — Python parser drops the full grammar block; only the `@PMO /resume <id>` token routes. The verbose grammar in the PA/PM v3 role-def DIRECTIVE EMISSION section is documentation of intent, not parser-recognized syntax.

**How to apply:** Every role emits ONE last-line `@PMO /resume <id>` when handing off. Resolve `<id>` from session-registry (`5ae2759b-795d-41b9-b730-6b1eeeabb83f`). Note + rationale belong in memsys memory, not in the directive line.

Supersedes-target: the DIRECTIVE EMISSION block in `pmo-role-pa-v1` (`897c696d`) and any parallel section in PM v3 (`127793ef`). Role-defs should be updated next revision.
