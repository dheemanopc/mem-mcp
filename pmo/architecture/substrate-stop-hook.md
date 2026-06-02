---
source: memsys (team: pmo)
id: b194ba79-1602-449f-b06a-398220d31ca1
type: decision
version: 2
is_current: True
created_at: 2026-06-01T17:41:08.864626Z
updated_at: 2026-06-01T17:41:08.864626Z
tags: [current, date:2026-06-01, for-da, for-do, for-future-modules, for-pm, indexable-false, infrastructure, kind:technical, pmo, pmo-substrate, project-manifest, project:pmo-v1-build, reference, routing, stop-hook, v1.1]
extracted_at: 2026-06-02
---

# Substrate Stop-Hook v1.1 — Routing Fixes (REFERENCE for future PMO modules)

**Supersedes `1aef7db2`** (formal `memory_supersede` follows this write — separated from the write because `supersedes` parameter on memory_write was 502-ing today, likely transactional overhead). Original was filed from the first consuming-project routing test; PM directive (2026-06-01): future memsys PMO modules will use this material as reference, so the memo must be project-agnostic on runtime noise while preserving the full session work. This version scrubs consuming-project session IDs and local file paths, adds the diagnostic recipes that surfaced each bug, and opens the canonical-location question explicitly.

**Type:** `decision` + `indexable=false` (skip embedding; lexical/keyword/tag/slug search still works).

**Note:** A parallel `fact`-typed copy was also written as `6c20793a` for the future-modules-cite-by-fact convention. They contain the same body. PMO PM may consolidate (delete `6c20793a` or this one) once the canonical type is decided.

**State:** `for-pm` — sequence whatever ratification / canonical-source-of-truth / backport work this needs. **Reference role:** future modules consuming the PMO substrate (new project bootstraps, alternate role sets, alternate orchestrators) should treat this memo as the "things that will silently break if you don't preserve them" list.

Cross-refs:
- **Full debug write-up + every iteration's evidence** lives in the first consuming project's team (school-homework-app): memo `35a097eb`. Read that for the chronological discovery story, the pane-capture evidence, and the wrong-hypotheses we ruled out.
- **Memsys-core bug surfaced in the same session** (`reminders_resolve/dismiss/snooze` all return `-32603 internal error` for one pending reminder): `a89ddcf5` in the `memsys-core` team. Filed up the consuming-domain → core route, not PMO scope.
- **Memsys-core write-path 502 pattern observed today** when `supersedes` parameter on `memory_write` is paired with ~14KB content; the same payload writes fine without `supersedes`, and `memory_supersede` works as a separate call. Worth surfacing to memsys-core domain.

## ONE-LINE STATE

Substrate `pmo-stop-hook.sh` had **FIVE distinct silent-failure bugs** compounded by **ONE grammar drift** between the role-prompt template and the hook's parser. All six are fixed in the deployed substrate AND re-embedded in the canonical megaprompt (round-trip verified bytes-for-bytes). Validated end-to-end via PM → Developer → PM round-trip in the first consuming project.

## SECTION 1 — THE SIX FAILURES, EACH WITH CAUSE / SYMPTOM / FIX / WHAT NOT TO REGRESS

### F1. Duplicate Stop-hook configuration

- **Symptom:** `tmux send-keys` of `/resume <id>` ran twice in succession, doubling the typed text in the prompt buffer.
- **Cause:** Claude Code fires user-level AND project-level Stop hooks. Both `~/.claude/settings.json` (user-level → substrate hook) AND a project-level `.claude/settings.json` (pointing at a project-copy of the same hook) were registered.
- **Fix:** Single source of truth — keep only user-level. Project-level must be `{}` or no `Stop` key.
- **Future-module guard:** When bootstrapping a new project, do NOT also configure a project-level Stop hook. The megaprompt's STEP B now writes only the user-level config; STEP D's project scaffolding must not silently re-introduce one.

### F2. Stop-hook flush race — final assistant text not on disk yet

- **Symptom:** Hook trace shows `assistant_text_len=0`, `directive_line=[]`, `cmd=[]`. Hook exits silently at the empty-cmd guard. JSONL transcript a few seconds later clearly contains the `@pmo` line.
- **Cause:** Claude Code fires the Stop event BEFORE the final text-bearing assistant entry is durable in the JSONL transcript. `tail -1` of `"type":"assistant"` lines picks up an earlier `tool_use` entry whose content has no `text` block; `jq` returns empty text.
- **Fix (layered, both needed):**
  1. Initial unconditional `sleep 0.5` at the top of the hook — protects the case where the previous entry is still the tail because the latest hasn't flushed yet (the polling guard below CAN'T detect this case because the previous entry HAS text content, just stale).
  2. Polling guard around the `tail -1 | jq` read — if first read returns empty text, sleep 100ms and retry up to 10 times (1s total). Protects the case where the latest entry is being-written and currently empty.
- **Future-module guard:** Don't remove the initial settle thinking the polling guard is enough — they catch DIFFERENT failure modes. The settle handles "latest hasn't appeared yet"; the polling handles "latest exists but is empty/partial". If a future Claude Code version moves the Stop event to AFTER the flush, the settle becomes zero-cost overhead and can be reduced; until then, keep both.

### F3. Bracketed-paste detection — `Enter` becomes "newline" not "submit"

- **Symptom:** `tmux send-keys -t %0 "/resume <id>" Enter` runs rc=0, but the pane shows `/resume <id>` typed correctly with the cursor on a NEW line below — input now multi-line; nothing submitted.
- **Cause:** Claude Code's TUI uses bracketed-paste detection. When text characters + Enter arrive in a single rapid `send-keys` burst, the TUI treats it as a paste; Enter becomes "newline within input" rather than "submit". Human-typed Enter (slow, discrete) submits; programmatic burst does not.
- **Fix:** Split the send into two `send-keys` calls with a 500ms sleep between:
  ```
  tmux send-keys -t "$pane" -l "$cmd"   # literal text only (-l disables key-name lookup)
  sleep 0.5
  tmux send-keys -t "$pane" Enter       # discrete keystroke event
  ```
  The 500ms is enough for the TUI to close its paste-detection window. `Enter` then arrives as a discrete key event and triggers submit.
- **Future-module guard:** CR-vs-LF was a red herring — `Enter` IS CR (`\r`, `^M`, ASCII 13), and that's correct. Don't replace `Enter` with `C-j` (LF) thinking that fixes it; the issue was *batching*, not key code. Don't reduce the sleep below ~0.3s; under that, paste-detection re-merges.

### F4. Dirty input buffer — residue from prior failed-routing attempts

- **Symptom:** After F3 was fixed, the very next submit went through but failed with `Session XXX/resume YYY/resume ZZZ was not found.` — three session IDs concatenated as a single arg.
- **Cause:** Previous failed routing attempts (when F3 was still broken) left their `/resume <id>` lines accumulated in the prompt input as newlines. When the F3-fixed Enter finally DID submit, it submitted the entire multi-line buffer as one input. Claude Code's `/resume` parser took everything after the first `/resume ` as the session-id arg.
- **Fix:** Before sending the literal text, wipe the input buffer:
  ```
  tmux send-keys -t "$pane" Escape        # exit any active edit/paste mode
  tmux send-keys -t "$pane" C-u C-u C-u   # clear up to three accumulated lines
  sleep 0.1
  ```
  `Escape` defangs any open edit state (some TUIs distinguish "input mode" from "command mode"). Three `C-u`s clear up to three multi-line accumulations; one is usually enough but three is a cheap belt-and-braces.
- **Future-module guard:** Never assume the input buffer is empty at hook fire time. If a future fix tries to remove the wipe ("it's not needed when other bugs are fixed"), it's wrong — the buffer state at Stop time depends on the user's prior interactions and is not guaranteed clean. Keep the wipe.

### F5. Stop hook fires twice for one logical routing intent

- **Symptom:** Observed cases where the same `cmd` was sent twice in quick succession even after F1 was resolved (single hook config).
- **Cause:** Claude Code's Stop event can fire more than once for what appears to be one response, depending on internal tool-call resolution patterns. Not fully characterized; assume it can happen.
- **Fix:** Idempotency guard. After each successful `send-keys`, record `now_ts \t cmd` to `/tmp/pmo-stop-hook-last-cmd`. On entry into the send section, if `cmd` equals the last recorded `cmd` AND less than 120s have elapsed, skip silently with a trace log entry.
- **Future-module guard:** The 120s window is a soft default — long enough to absorb realistic double-fires, short enough that a deliberate re-route to the same target later in the session still fires. If a future module needs to *re-route immediately* to the same target (e.g. retry semantics), this guard would block it; the right fix is a per-cmd reset signal, not removing the guard.

### F6. Grammar drift between role-prompt template and hook parser

- **Symptom:** Roles emit V3-grammar directives (per the v3 role-prompt template, e.g. `pmo-role-pm-v1` v3), hook silently ignores them, routing-doesn't-fire is the worst-case for a silent grammar mismatch.
- **Cause:** Role-prompt template v3 (`127793ef` etc.) teaches the canonical V3 grammar:
  ```
  @PMO NEXT     resume=<id> role=<role> note="..."
  @PMO ESCALATE resume=<id> role=<role> ref=<mem> note="..."
  @PMO SPAWN role=<role> [domain="..."] note="..."
  @PMO DONE artifact=<slug> note="..."
  @PMO REVIEW_REQUEST note="..."
  ```
  The substrate hook (pre-fix) only parsed the V2 lower-spec `@pmo /resume <id>` form (case-sensitive lowercase). Drift was silent for both directions.
- **Fix:** Hook now parses BOTH grammars, case-insensitive on the `@PMO`/`@pmo` marker:
  - **V3 NEXT/ESCALATE:** extract `resume=<id>` from anywhere in the directive, construct `/resume <id>`.
  - **V2 fallback:** match `@pmo /resume <id>` (kept so older role-prompts and ad-hoc emits still work).
  - **SPAWN / DONE / REVIEW_REQUEST:** NOT auto-routed (by design — SPAWN needs orchestrator action, DONE is terminal, REVIEW_REQUEST is human-gated). Hook silently passes; the human reads and acts.
- **Future-module guard:** When adding a new directive verb to the V3 grammar, decide explicitly at design time whether it carries a `resume=<id>` (auto-routable) or not (no-op pass-through). Don't add a new auto-routable verb without also extending the hook's parser. Don't downgrade role-prompt templates to V2 thinking that simplifies things — it loses the role/note/ref metadata the V3 grammar carries for downstream auditability.

## SECTION 2 — DIAGNOSTIC RECIPES THAT FOUND THESE BUGS

For future modules debugging "routing doesn't fire" symptoms, these are the recipes that worked. None of them required external tooling beyond `bash`, `tmux`, `jq`, and the transcript JSONL file Claude Code already maintains.

1. **Entry-trace at the top of the hook.** Write a timestamped line to `/tmp/pmo-stop-hook-trace.log` immediately after `set -euo pipefail`. If the trace is empty after a Stop event, the hook isn't being invoked (Claude Code not loading the config, permission gate, etc.). If the trace has entries, the hook IS firing — narrow further.

2. **PRE-GUARD trace.** Just before the early-exit guard, log `assistant_text_len`, `directive_line`, and `cmd`. This single line tells you which parse stage is failing — if `len=0`, transcript-read failed (flush race); if `len>0` but `directive_line` empty, grep didn't match (grammar drift); if `directive_line` matches but `cmd` empty, regex didn't extract (grammar drift in the verb form).

3. **Post-send `send-keys` exit codes + delayed pane capture.** After each `send-keys`, log `rc`. Then spawn `( sleep 2 && tmux capture-pane -t "$pane" -p | tail -5 >> trace.log ) &` so the trace log shows what's IN the pane 2 seconds after the send. This is what revealed F3 (the `/resume` text typed but on the wrong line) and F4 (accumulated multi-line residue).

4. **Replay the parse pipeline against the recorded JSONL.** Take the trace's recorded transcript path, find the assistant line that was last at hook fire time (timestamp from the trace log), run the hook's `grep | tail -1 | jq | grep | regex` pipeline against that line directly in a shell. If your replay produces the right `cmd`, the hook's *parse* is right and the issue is later (send-keys, pane, TUI). If replay also produces empty, the parse is wrong (flush race or grammar drift).

5. **Hook simulation with mock stdin.** Pipe `echo '{"transcript_path":"<path>","session_id":"...","stop_hook_active":false}' | bash -x /path/to/pmo-stop-hook.sh 2>&1` to see every shell-trace line. This is what surfaced F2 (the jq pipeline silently returning empty on a tool_use-only entry).

## SECTION 3 — WHAT'S DEPLOYED, WHAT'S NOT, WHAT'S OPEN

**Deployed in the substrate (`~/pmo-substrate/pmo-stop-hook.sh`) as of 2026-06-01:**
- All six fixes (F1–F6)
- Verbose trace logging to `/tmp/pmo-stop-hook-trace.log` (entry line, PRE-GUARD values, directive/cmd/pane echoes, send-keys exit codes, post-send pane snapshot 2s later)
- Idempotency-guard sentinel at `/tmp/pmo-stop-hook-last-cmd`

**Re-embedded in the canonical megaprompt (STEP A base64 block):**
- Same hook content; round-trip verified bytes-for-bytes between embed and deployed substrate
- v1.1 changelog note prepended in the megaprompt header summarizing all six fixes for future installers

**Open questions for PMO PM (not in PM-of-consuming-project scope):**

1. **WHERE is the canonical megaprompt?** The version that was updated in this session lives under a consuming project's local directory. If PMO has a canonical source-of-truth (PMO repo, substrate package, build artifact), the fixes need to land there too. If PMO does NOT have a canonical location yet, that's itself an open question worth surfacing — without one, the next consuming project may re-discover all six bugs. (Possible answer: store the megaprompt itself in memsys as a fact + indexable=false, slug `pmo-megaprompt-v1-1` or similar.)

2. **README / INSTALL doc drift.** The substrate's README claims the hook "pre-fills `/resume <id>`... for the human to **Enter**" — that's the OLD design intent. The v1.1 fixed hook auto-Enters via the separated-keystroke technique (F3 fix). Either the doc needs updating to match the auto-Enter behavior, or the auto-Enter needs to be made configurable (e.g. an `auto_enter=true` flag) and the original "pre-fill only, human submits" design preserved as an option. This is a PMO domain product call, not a PM-of-consuming-project call.

3. **Trace logging level for v1.5.** Currently verbose at v1.1 (entry, PRE-GUARD, directive/cmd/pane, send-keys rc, pane snapshot). Cheap to keep, valuable when something doesn't work. For v1.5 cleanup, decide whether to: (a) keep verbose, (b) gate behind `PMO_HOOK_DEBUG=1` env, or (c) trim to entry + send-keys rc only.

4. **Project-level Stop hook nuke in STEP B.** The v1.1 megaprompt warns installers not to also configure a project-level Stop hook, but STEP B doesn't actively nuke a pre-existing one. Cleaner: STEP B should check `<project>/.claude/settings.json` and, if a Stop hook exists, either remove it or set it to `{}` after confirming with the operator.

5. **Recurring vs one-shot routing intent.** The 120s idempotency window (F5 fix) prevents accidental double-fires but also blocks deliberate immediate re-routing to the same target. Future modules with retry semantics may need a per-cmd reset signal — leave the guard, add the reset path separately.

## SECTION 4 — PROPOSED ROUTING-TEST CHECKLIST FOR v1.1 VALIDATION

Tag this as the canonical "did the substrate install actually work?" check. Future modules should run this in their bring-up validation set, not just unit tests against the hook's parse functions — F1, F3, F4 are integration-level and only surface against a real Claude Code TUI + real tmux pane.

1. Install substrate via megaprompt STEP A; confirm `~/pmo-substrate/pmo-stop-hook.sh` exists, is executable, and matches the embedded base64 bytes-for-bytes.
2. STEP B writes user-level Stop hook only; confirm `<project>/.claude/settings.json` does NOT also configure a `Stop` hook (or its `hooks.Stop` is empty/missing).
3. STEP D scaffolds project; STEP E stands up the roles.
4. From PM session, emit `@PMO NEXT resume=<another-role-id> role=<role> note="routing check"` as the last line of a response.
5. Confirm `/tmp/pmo-stop-hook-trace.log` shows: PRE-GUARD with non-empty `cmd`, `send-keys rc=text:0,enter:0`, pane snapshot 2s later showing the prompt empty (or in the new session's prompt).
6. Confirm the window actually switched to the target role's session.
7. From the target role, emit the directive back to PM; confirm round-trip.

If any of the seven steps fails, the trace log + the diagnostic recipes in Section 2 should localize which fix regressed.

## SECTION 5 — WHY INTEGRATION-LEVEL BUGS LANDED FROM A CONSUMING PROJECT

For the framework health discussion (PMO PM scope): F1 (duplicate config), F3 (bracketed-paste behavior), and F4 (dirty buffer) cannot be caught by unit tests against the hook's parse pipeline. They require: a real Claude Code TUI, a real tmux pane, a real Stop-event timeline, and a real user iterating across failed attempts (which is what created the buffer residue in F4). The PMO project's v1.1 closure should include the routing-test checklist (Section 4) in its validation set, run against at least one real bootstrap, not just isolated unit tests. The first consuming project's routing test was effectively that validation run — bugs that landed there are what unit tests structurally cannot catch.

End of memo.
