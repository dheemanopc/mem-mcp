# Security review sign-off log

T-6.11. Per-PR sign-off that the security tests in tests/security/ are
correct, not just passing. Reviewed by operator (Anand) before merge.

## Why this exists

A security test that runs but doesn't actually verify the property it
claims to verify is worse than no test (false sense of safety). E.g.:
- `test_cross_tenant_search_isolation` requires the mcp_client fixture
  to be wired to a real MCP endpoint — until then, the test passes
  trivially because the stub returns empty results.
- `test_rls_failclosed_select_without_tenant_context` needs MEM_MCP_TEST_DSN pointing at a real
  Postgres with RLS enabled — without that, the test skips, not fails.

This file documents which security tests are FULLY VERIFIED vs SKELETONS.

## Test status table

| Test (file:test_name) | Status | Verified | Reviewed by | Date |
|---|---|---|---|---|
| test_cross_tenant_search.py::test_cross_tenant_search_isolation | SKELETON | needs live MCP client | — | — |
| test_cross_tenant_search.py::test_cross_tenant_get_by_id | SKELETON | needs live MCP client | — | — |
| test_cross_tenant_search.py::test_cross_tenant_stats | SKELETON | needs live MCP client | — | — |
| test_sqli_probes.py::test_sqli_payloads_passed_as_parameters_not_sql | VERIFIED | static AST check | Anand | 2026-05-03 |
| test_sqli_probes.py::test_sqli_probes_in_search_tags[payload-0] | SKELETON | needs live DB + MCP | — | — |
| test_sqli_probes.py::test_sqli_probes_in_write_content[payload-0] | SKELETON | needs live DB + MCP | — | — |
| test_sqli_probes.py::test_sqli_probes_in_metadata[payload-0] | SKELETON | needs live DB + MCP | — | — |
| test_rls_failclosed.py::test_rls_failclosed_select_without_tenant_context | SKELETON | needs live DB | — | — |
| test_rls_failclosed.py::test_rls_failclosed_count_without_tenant_context | SKELETON | needs live DB | — | — |
| test_rls_failclosed.py::test_rls_failclosed_with_tenant_context | SKELETON | needs live DB | — | — |
| test_pool_isolation.py::test_pool_does_not_leak_tenant_context | SKELETON | needs live DB | — | — |
| test_pool_isolation.py::test_pool_isolation_under_concurrent_load | SKELETON | needs live DB | — | — |
| test_pool_isolation.py::test_pool_connection_reuse_clears_context | SKELETON | needs live DB | — | — |
| test_scope_enforcement.py::TestReadScopeEnforcement::test_read_tool_rejected_without_memory_read[tool_class0] | VERIFIED | mock-based | Anand | 2026-05-03 |
| test_scope_enforcement.py::TestReadScopeEnforcement::test_read_tool_rejected_with_only_write_scope[tool_class0] | VERIFIED | mock-based | Anand | 2026-05-03 |
| test_scope_enforcement.py::TestWriteScopeEnforcement::test_write_tool_rejected_without_memory_write[tool_class0] | VERIFIED | mock-based | Anand | 2026-05-03 |
| test_scope_enforcement.py::TestWriteScopeEnforcement::test_write_tool_rejected_with_only_read_scope[tool_class0] | VERIFIED | mock-based | Anand | 2026-05-03 |
| test_scope_enforcement.py::TestStatsToolScopes::test_stats_tool_requires_read_scope | VERIFIED | mock-based | Anand | 2026-05-03 |
| test_scope_enforcement.py::TestTestToolScopes::test_echo_tool_requires_read_scope | VERIFIED | mock-based | Anand | 2026-05-03 |
| test_scope_enforcement.py::TestTestToolScopes::test_echo_tool_succeeds_with_read_scope | VERIFIED | mock-based | Anand | 2026-05-03 |
| test_token_reuse.py::test_revoked_token_rejected_after_global_signout | SKELETON | needs live Cognito | — | — |
| test_token_reuse.py::test_token_with_future_exp_rejected | SKELETON | needs live Cognito | — | — |
| test_token_reuse.py::test_jwt_signature_validation_rejects_tampered_token | SKELETON | needs unit test wiring | — | — |
| test_token_reuse.py::test_expired_token_rejected | SKELETON | needs unit test wiring | — | — |
| test_tenant_status.py::TestTenantStatusEnforcement::test_suspended_tenant_returns_403_with_account_suspended | VERIFIED | mock-based via FakeResolver | Anand | 2026-05-03 |
| test_tenant_status.py::TestTenantStatusEnforcement::test_pending_deletion_tenant_returns_403_with_deletion_pending | VERIFIED | mock-based via FakeResolver | Anand | 2026-05-03 |
| test_tenant_status.py::TestTenantStatusEnforcement::test_active_tenant_succeeds | VERIFIED | mock-based via FakeResolver | Anand | 2026-05-03 |

## Sign-off statement (per release)

When tagging a release, the operator appends an entry below confirming:
1. They have read the test code (not just the pass/fail summary).
2. The assertions actually verify the security property claimed in the docstring.
3. SKELETON tests are tracked (with infrastructure dependencies noted) and graduated to VERIFIED before public launch.

### v0.1.0 (closed beta) — pending
- Reviewer: Anand
- Date: <pending T-10.1>
- Status: pre-launch — most live-DB skeletons not yet verified; closed-beta acceptable
- Action items before public launch:
  - [ ] Wire mcp_client fixture to real MCP endpoint
  - [ ] Provision MEM_MCP_TEST_DSN for nightly CI
  - [ ] Run `pytest tests/security --live-aws` against staging; verify all parametrized SQLi probes return only tenant-A rows
  - [ ] Run live RLS fail-closed test; confirm 0 rows without tenant context
  - [ ] Run live token reuse test against ci pool; confirm GlobalSignOut invalidates within 30s
