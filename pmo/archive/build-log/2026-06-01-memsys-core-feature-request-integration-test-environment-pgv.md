---
source: memsys (team: pmo)
id: 23259bee-bb13-4cd6-8002-3fa1668b249f
type: decision
version: 1
is_current: True
created_at: 2026-06-01T04:51:17.118080Z
updated_at: 2026-06-01T04:51:17.118080Z
tags: [pmo, memsys-core, feature-request, for-memsys-core, pmo-surfaced-core-gap, integration-test-env, operator-runbook, pgvector, test-dsn, pmo-project-pmo-v1-build, v1, current]
extracted_at: 2026-06-02
---

# memsys-core FEATURE REQUEST — Integration-test environment: pgvector-backed test instance + `MEM_MCP_TEST_DSN` + plugin-test fixture runbook

**Filed 2026-06-01 by PMO DO, threaded under manifest `75e8523c`. State: `for-memsys-core` / `feature-request`. Surfaced from the PMO plugin track, where it is the single root cause of seven lower-assurance task closures (T1 deferred → T2–T7 all VERIFIED-WITH-RECORDED-GAPS). Sibling to prior PMO-surfaced core gaps `3d1145c7` (write-gap, shipped), `36ac16a1` (on_startup, shipped), `edeae913` (plugin-onboarding bootstrap, open). This one is INFRA/operator-enablement, not an SDK-surface change. memsys-core owns scoping + implementation.**

Refs: PMO spine-complete handoff `46f91aa6` | DA T2+T3 verification (sets the lower-assurance closure pattern) `d038512c` | DA T5+T6 verification + spine-complete `f5d94bff` | DA T4+T7 verification `e225a631` | standing verification standard `a32ac9f0` | T1 closure bar `584614ac` | DO ledger + first escalation of this ceiling `077a500a`.

## TYPE

Infrastructure / test-enablement. NOT an SDK contract change, NOT a schema or migration change to the product. The ask is a TEST ENVIRONMENT + the operator runbook to run the existing (already-authored) integration suites against it. The PMO plugin code is complete and its integration tests are written; they cannot be canonically executed because the test substrate doesn't exist.

## THE GAP (why every PMO task closed at lower assurance)

Seven PMO plugin-track tasks (T1–T7) are code-complete and DA-verified, but six of them closed as **VERIFIED-WITH-RECORDED-GAPS** rather than canonical VERIFIED, for ONE shared reason: the integration test suites are gated and have never been canonically run.

- Every PMO integration test skips when `MEM_MCP_TEST_DSN` is unset (the pattern T1 established and T2–T7 inherited).
- That DSN, and the fixture wiring it implies, does not exist in any reachable environment. So the suites skip everywhere — CI (no DSN by design) AND local/prod (no operator runbook).
- The result is a **systemic assurance ceiling**: each task proves its core paths empirically (substrate-side, by hand) and via SDK-independent unit tests, then records the canonical-run gap as a named obligation. The DA flagged this in `d038512c`/`f5d94bff` as a recurring ceiling that is a PM/owner/infra decision, not the DA's to mandate.

The recorded obligations currently outstanding (all dischargeable by this one environment landing):
- T3-O1: live `get_batch` partial-failure demux demonstration.
- T4-O1: `check_permission` integration tests (needs `mem_mcp` SDK only — NO DSN; dischargeable on prod even before the full runbook).
- T5-G1: refs traversal / bad-parent reject / hard-delete block.
- T6-G1: live permission-denial end-to-end (also needs DO matrix content seeded).
- T7-G1: registration leaf roundtrip / re-registration recency / self-discovery exclusion.
- (T2-O1 already discharged via cross-domain deploy confirm `3566e1fd`.)

## WHAT IS REQUESTED

A one-time test-environment stand-up that lets the gated suites run canonically:

1. **A pgvector-backed test database instance** — a Postgres with the `pgvector` extension installed, schema-migrated to current memsys-core HEAD, isolated from prod data. (pgvector is required because the substrate generates embeddings on indexable writes; the integration tests exercise the real write/search/list/thread_get/get_batch paths, which touch the vector column.)

2. **A published `MEM_MCP_TEST_DSN`** pointing at that instance, available to the test runner (CI secret and/or operator-local env), so `pytestmark = skipif(not MEM_MCP_TEST_DSN)` un-skips.

3. **The plugin-test fixture runbook** — the operator steps + fixtures that let a plugin's `tests/integration/` run against the instance. Specifically the fixtures the PMO suites currently `pytest.skip` on (named in handoff `46f91aa6`): `memories` (a real `MemoryClient` against the test DSN), plus per-suite seed fixtures (`test_manifest_root`, `seeded_manifest_root`, `seeded_role_def_*`, `seeded_matrix_slug`/`seeded_matrix_team_id`, `seeded_work_item_id`/`seeded_identity`, `seeded_parent_id`). These are PMO-side to author, but they depend on a documented pattern for "construct a real `MemoryClient` against the test instance and tear down test data" that only memsys-core can authoritatively define.

## SCOPE NOTE — TWO THINGS memsys-core SHOULD CONFIRM (DA's caveat, carried)

Per DA `f5d94bff`: standing up the pgvector container satisfies the DSN condition but does NOT by itself (a) wire the stubbed fixtures, nor (b) confirm the SDK→substrate path runs WITHOUT full app context. So the request explicitly includes confirming/documenting:
- How a plugin integration test constructs a working `MemoryClient` against the test DSN outside the full FastAPI app lifespan (or whether minimal app context must be booted).
- Whether `on_startup` hooks / plugin registration need to run for the SDK client path to function in tests, or whether the client can be built standalone against the pool.

These two are the difference between "a database exists" and "the suites actually go green," and they're memsys-core knowledge.

## WHY IT MATTERS / IMPACT

- **Retro-upgrades all seven PMO closures** from VERIFIED-WITH-RECORDED-GAPS to canonical VERIFIED, in one move, by enabling the already-written suites to run.
- **Generalizes beyond PMO**: ANY future memsys-core plugin (the PMO track is the first, not the last) hits this same wall on day one. A documented plugin-integration-test runbook + test instance is reusable infrastructure, not PMO-specific scaffolding. This is arguably the highest-leverage item because it's a capability the whole plugin ecosystem inherits.
- **Closes a known audit debt** cleanly rather than letting recorded gaps accumulate silently across future tasks.

## PRIORITY / SEQUENCING (memsys-core's call, with context)

NOT on the Monday demo critical path — the demo is by-simulation of the PROCESS (`d7a6c240`) and runs fine at current assurance; lower-assurance closures are acceptable for it. So this is **post-demo / product-hardening priority**, not emergency. But it is the right next infra investment if the PMO plugin (and the plugin platform generally) is meant to be a durable production system rather than a demo artifact. Owner indicated memsys-core will take this.

## NOT IN THIS REQUEST

- No SDK surface change (the SDK is at Tier-2 parity, sufficient — SF-1..16).
- No product schema/migration change.
- No PMO-plugin code change (the plugin is code-complete; only its test execution is blocked).
- The PMO-side fixture authoring is PMO's to do ONCE the runbook pattern + instance exist; this request is the enablement those fixtures depend on.

## ASK

memsys-core to scope + stand up the pgvector test instance + `MEM_MCP_TEST_DSN` + the plugin-integration-test fixture runbook (with the two confirmations in the SCOPE NOTE), as reusable plugin-platform infrastructure. On landing, PMO discharges T3-O1/T4-O1/T5-G1/T6-G1/T7-G1 by running the existing suites, and the seven task closures upgrade to canonical VERIFIED.
