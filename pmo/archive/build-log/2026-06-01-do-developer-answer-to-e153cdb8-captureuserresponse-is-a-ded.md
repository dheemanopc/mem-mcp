---
source: memsys (team: pmo)
id: bb55f05b-944c-407a-9c2d-0ef458aedef9
type: decision
version: 1
is_current: True
created_at: 2026-06-01T05:35:47.599436Z
updated_at: 2026-06-01T05:35:47.599436Z
tags: [pmo, do-to-developer, for-developer, capture-interface, closes-e153cdb8, pmo-project-pmo-v1-build, v1, current]
extracted_at: 2026-06-02
---

# DO → Developer — ANSWER to e153cdb8: capture_user_response is a DEDICATED FUNCTION, not convention-only

**Written 2026-06-01 by DO (PMO domain), threaded under manifest `75e8523c`. State: `for-developer`. Resolves the long-open Developer→DO escalation `e153cdb8` on the capture-interface shape. Ruling: dedicated function. This matches the shipped code (per T2 impl-response `e6df13a1` / handoff `46f91aa6`, `capture_user_response` is already implemented as a dedicated function under the pending-DO marker), so the answer is CONFIRM-AS-SHIPPED, not a change.**

Refs: Developer escalation `e153cdb8` | DO lean recorded `b29f1a10` | PM working-memory ruling `ba6d113a` | Area A vocabulary `940cfbae` (§5 working-memory discipline, §7 tag set) | T2 trio `780ea619`/`2d05c14e`/`542b1c74` | T2 impl-response `e6df13a1`.

## THE QUESTION

Is `capture_user_response` (persist the owner's words verbatim as a `pmo-user-response` working memory) a DEDICATED FUNCTION on the T2 helper surface, or is it CONVENTION-ONLY (callers just use `write_working` with the `pmo-user-response` working-type tag)?

## RULING — DEDICATED FUNCTION. Three reasons.

1. **The verbatim contract is a framework concern, not a caller responsibility.** Capturing the owner's exact words is load-bearing: it is the audit trail of intent, and the PM ruling (`ba6d113a`) makes `pmo-user-response` a REQUIRED write class. A dedicated function makes the verbatim discipline enforceable at one site — no normalization, no trimming, no "helpful" reformatting — rather than trusting every caller to remember not to mangle the text. Convention-only spreads a correctness-critical contract across N call sites.

2. **It is the one working-type with a semantic guarantee beyond tagging.** Every other working memory is "content + the right tags." `pmo-user-response` additionally guarantees byte-for-byte fidelity of the owner's input. That extra guarantee deserves a named function whose contract IS that guarantee (the T2 test plan already drafted `test_capture_passes_text_verbatim` for exactly this — whitespace/unicode/emoji/newlines through unchanged).

3. **It aligns with shipped code — zero churn.** Per the T2 impl-response, `capture_user_response` is already implemented as a dedicated function (held under the pending-DO marker). Ruling "dedicated function" CONFIRMS the shipped state. Unskip the sentinel tests (`test_capture_user_response_pending_do.py` + the integration counterpart); no code deletion. Had I ruled convention-only, it would have been a one-line deletion + caller migration — avoidable churn for no benefit.

## WHAT THE DEVELOPER DOES

- Treat `e153cdb8` as RESOLVED: `capture_user_response` stays a dedicated function.
- Remove the pending-DO marker: unskip `test_capture_user_response_pending_do.py` and `test_capture_user_response_roundtrip.py`; they become live T2 tests.
- The function's contract (for the impl-response / docstring): persists the caller-supplied text VERBATIM (no normalization) as a `pmo-user-response` working memory via the `write_working` path — `indexable=false`, threaded under the manifest, standard 5 tags with `pmo-user-response` as the working-type tag (Area A §7). It is a thin verbatim-guaranteeing wrapper over `write_working`, not a separate write path.
- This is a T2-surface confirmation; it does NOT reopen T2's verification gate (the mechanism was already verified; only the pending-DO marker is being cleared). Note the resolution in the next convenient impl-response or a short discharge memo; no new gate cycle needed.

## CONSISTENCY NOTE

Area A §5 already lists `pmo-user-response` as an always-write class and §7 lists it as a working-type tag. This ruling is consistent with both — the dedicated function is the mechanism by which the §5 "always capture verbatim" discipline is honored. No Area A change needed.

`e153cdb8` is now closed. The last open item on the DO content track is the D1 sealed-fallback note (separate memo, routed to DA for composition-check).
