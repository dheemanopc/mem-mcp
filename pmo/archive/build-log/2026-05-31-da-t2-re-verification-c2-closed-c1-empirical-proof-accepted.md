---
source: memsys (team: pmo)
id: a32ac9f0-a4fb-439c-864f-e5ed9d6e3cb4
type: decision
version: 1
is_current: True
created_at: 2026-05-31T18:41:36.089211Z
updated_at: 2026-05-31T18:41:36.089211Z
tags: [current, da-to-developer, da-to-do, da-verification, for-developer, for-do, infrastructure, pmo, pmo-project-pmo-v1-build, pmo-task-T2, project-manifest, sdk-substrate-facts, sf-13, t2-verification-held, v1]
extracted_at: 2026-06-02
---

# DA T2 RE-VERIFICATION — C2 CLOSED; C1 empirical proof ACCEPTED in principle (canonical run owed); C3 open. T2 still HELD but materially advanced. New SF-13 (indexable≠lexical-invisible).

Written 2026-05-31 by PMO DA, manifest 75e8523c. State: for-developer / for-do. Rules on Developer T2 verification amendment 21e55047 against the three conditions in HELD verdict e81c4332. Verdict: T2 remains HELD (not yet VERIFIED), but C2 is closed and C1 is empirically satisfied-in-principle. Only C3 (human review+merge+prod re-confirm) and the canonical integration run remain.

Refs: T2 amendment 21e55047 | T2 HELD verdict e81c4332 | impl-response e6df13a1 | design ratification 1e24baec | spec 4eb18941 | T1 closure precedent 584614ac | sentinel 0c2876ea (created+deleted during amendment).

## C2 — ACCEPTED, CLOSED
Test I7 (test_write_retrievable_via_list_memories_tag_filter) added at commit 7e3ec72: asserts list_memories(tags=[...], indexable=False, parent_id=manifest) returns the written ID and every returned row is indexable=False (strict-AND, SF-10 native, no app-side filtering). This closes the 1e24baec carry-forward AT T2. T3 inherits a proven property. Done.

## C1 — EMPIRICAL PROOF ACCEPTED IN PRINCIPLE; CANONICAL RUN STILL OWED
The Developer could not run pytest tests/integration/ (no DSN/operator runbook — the T1+T2 deferral pattern). Instead they wrote a real working memory (sentinel 0c2876ea) through the LIVE substrate with the EXACT kwargs build_working_write_kwargs() produces, and ran the suite's assertions. I ACCEPT this as legitimate empirical proof of substrate behavior:
- DoD-1 (leaf under root + exact 5-tag set + indexable=False) — ACCEPTED. Persisted indexable=False confirmed in storage; embedding_status:skipped_opt_out corroborates (no embedding generated).
- DoD-2 (tag-filter retrieval) — ACCEPTED. list returned the sentinel under strict-AND.
- DoD-3 (semantic search does NOT return it) — ACCEPTED FOR ITS SEMANTIC INTENT, WITH A CAVEAT (see SF-13). Semantic-style query did not surface the sentinel; only indexable=True peers returned.

CAVEAT — I will not let the DoD-3 reframe pass silently. DoD-3 as literally written ("semantic search does NOT return the write") is FALSE at the lexical layer: the Developer found that an exact-phrase keyword query DID surface the sentinel (keyword score 0.003), because indexable=False suppresses EMBEDDING/semantic ranking but NOT lexical/keyword match on stored content. The INTENDED property (working memories don't pollute semantic retrieval) holds; the LITERAL property (search returns nothing) does not. This is a real, narrow leak the framework must know about — not a substrate violation, but not something to wave away as "intent satisfied" without recording it. The Developer surfaced it honestly; credit to them for not hiding it behind the reframe.

CANONICAL RUN OWED: empirical-substrate proof closes C1 IN PRINCIPLE but does not substitute for the actual pytest integration suite against MEM_MCP_TEST_DSN. That run is FOLDED INTO C3's window — when a reviewer + DSN are available for merge, the suite runs for real (with the I4 query-design fix below). Until then C1 is "evidence accepted; operator-canonical run still owed."

## C3 — OPEN, correctly not Developer's to close
PR #1 (now commit 7e3ec72) OPEN + MERGEABLE + CI green (lint+unit, incl. the introspectable C2 test). Needs human reviewer → merge → prod plugin-discovery re-confirm (T1 precedent 584614ac). The owner declined to author the T2 PR review (human gate by design). Carried open.

## NEW SUBSTRATE FACT — SF-13 (ratified)
indexable=False suppresses EMBEDDING GENERATION and therefore SEMANTIC/vector ranking, but does NOT suppress LEXICAL/keyword matching on stored content. A working memory (indexable=False) CAN still be surfaced by an exact-phrase keyword query, though it will not appear in semantic-similarity results and carries no embedding.
CONSEQUENCES carried into T3..T7:
- Session-load and any semantic retrieval over the manifest will NOT pull working memories by similarity (the design intent holds).
- BUT no role may ASSUME working memories are wholly search-invisible — a lexical query on a known phrase can hit them. Anything requiring true invisibility (e.g. secrets) must NOT rely on indexable=False. Working memories are not secret; they are semantic-non-polluting. SF-13 records that distinction.
- Test design: DoD-3 / I4 must use a SEMANTIC-STYLE query (not a literal content substring) to assert the intended property. Fold into the Test Plan when I4 is re-authored under the operator runbook. (Developer flagged this; ratified as the test-author-time rule.)

## NET VERDICT
T2 = HELD, materially advanced. C2 closed. C1 empirically accepted; canonical pytest run owed (folded into C3 window). C3 open on the human review+merge+prod-reconfirm gate. NOT yet VERIFIED — I will issue VERIFIED when: (i) PR #1 reviewed+merged, (ii) the integration suite runs canonically against a DSN and DoD-1/2/3 pass (DoD-3 via semantic-style query per SF-13), (iii) prod plugin-discovery re-confirms the pmo helpers live. DO: keep T2 IN-FLIGHT; do not close.

## STANDARD-SETTING (for future verification gates — also a friction-log item)
Empirical-substrate proof (writing real kwargs through the live tool layer + running the suite's assertions manually) is ACCEPTED as in-principle evidence when the canonical integration run is operator-gated and unavailable in-session — PROVIDED it is labeled honestly as empirical-not-canonical (as the Developer did) and the canonical run remains owed at merge. It is NOT accepted as full closure. This becomes the standing C1-type standard for operator-gated integration tests across T3..T7.

Developer: C2 done; C1 accepted-in-principle; pursue C3 (reviewer+merge). On merge, run the canonical suite with the SF-13 I4 fix and refile for final VERIFIED. T3 trio is with the Reviewer; unaffected by this.
