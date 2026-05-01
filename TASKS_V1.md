# Memory MCP — Task List v1

Tasks aligned to `MEMORY_MCP_LLD_V1.md` and `MEMORY_MCP_BUILD_PLAN_V2.md` §17. Each task is intended to map 1:1 to a GitHub issue.

## Convention

- **ID**: `T-PHASE.GROUP.TASK[.SUB]`. IDs are stable; retire rather than reuse.
- **Status**: `[ ]` open, `[x]` done.
- **Priority**: `p0` (blocker) | `p1` (important) | `p2` (nice-to-have).
- **Areas**: `infra`, `auth`, `mcp`, `memory`, `web`, `ops`, `tests`, `docs`.
- **Per task fields**: `Phase · area · priority · deps · What · AC · Files`.

The convention for issues: title `[T-x.y.z] short summary`; body references this file + `MEMORY_MCP_LLD_V1.md` + `MEMORY_MCP_BUILD_PLAN_V2.md`. Labels: `phase-N`, `area-...`, `priority-pN`.

---

## Phase 0 — Prerequisites (operator-driven)

### T-0.1 [p0] [ops] Confirm Route 53 hosted zone for `dheemantech.in`
Phase 0 · ops · p0 · deps: —
- **What**: Confirm hosted zone exists in Route 53; capture zone ID for CFT param.
- **AC**: `aws route53 list-hosted-zones-by-name --dns-name dheemantech.in` returns the zone; ID recorded in `infra/cfn/parameters/prod.json` as `HostedZoneId`.
- **Files**: `infra/cfn/parameters/prod.json` (placeholder created here).

### T-0.2 [p0] [ops] AWS account hardening
Phase 0 · ops · p0 · deps: —
- **What**: Enable CloudTrail in `ap-south-1`; enable AWS Config; create operator IAM user with MFA; root locked.
- **AC**: CloudTrail events visible; Config recording; IAM user has MFA.
- **Files**: `docs/runbooks/account_hardening.md`.

### T-0.3 [p0] [ops] Bedrock Titan v2 model access
Phase 0 · ops · p0 · deps: —
- **What**: Enable `amazon.titan-embed-text-v2:0` in Bedrock console (`ap-south-1`).
- **AC**: `aws bedrock-runtime invoke-model --model-id amazon.titan-embed-text-v2:0` returns a vector.
- **Files**: `docs/runbooks/account_hardening.md` (note).

### T-0.4 [p0] [ops] SES verified domain `dheemantech.com` (sender)
Phase 0 · ops · p0 · deps: —
- **What**: Verify `dheemantech.com` (sending domain for `anand@dheemantech.com`); enable DKIM; create SES configuration set `mem-mcp-ses`.
- **AC**: domain status = verified; DKIM CNAMEs present; config set exists.
- **Files**: DNS in registrar/Route 53.

### T-0.5 [p0] [ops] SES sandbox-removal request
Phase 0 · ops · p0 · deps: T-0.4
- **What**: File AWS support ticket to remove SES sandbox limit.
- **AC**: ticket open; resolution tracked.

### T-0.6 [p0] [auth] Google OAuth client (existing project)
Phase 0 · auth · p0 · deps: —
- **What**: In Google Cloud Console (existing project), create OAuth 2.0 client; add redirect URI `https://memauth.dheemantech.in/oauth2/idpresponse`; record client_id+secret in SSM `/mem-mcp/cognito/google_client_{id,secret}`.
- **AC**: SSM params exist (SecureString for secret); test fetch via `aws ssm get-parameter`.
- **Files**: SSM (no repo file).

### T-0.7 [p0] [ops] Operator KMS key (CMK)
Phase 0 · ops · p0 · deps: —
- **What**: Create CMK alias `alias/mem-mcp` with rotation enabled (will be CFT'd in T-1.X but bootstrap key needed manually for CFT bootstrap-bucket SSE).
- **AC**: key reachable via `aws kms describe-key --key-id alias/mem-mcp`.

### T-0.8 [p1] [docs] Pre-deploy checklist doc
Phase 0 · docs · p1 · deps: T-0.1..T-0.7
- **What**: Author `infra/cfn/README.md` with manual prereqs from LLD §2.4 and operator workflow.
- **AC**: PR review.
- **Files**: `infra/cfn/README.md`.

---

## Phase 1 — CFT infrastructure

### T-1.0 [p0] [infra] CFN bootstrap-bucket stack
Phase 1 · infra · p0 · deps: T-0.7
- **What**: `infra/cfn/nested/090-bootstrap-bucket.yaml` — S3 bucket `mem-mcp-cfn-${AWS::AccountId}-aps1` with versioning, SSE-KMS, BPA-all, deny-non-TLS policy.
- **AC**: `cfn-lint` clean; `aws cloudformation deploy` succeeds; bucket reachable from `aws s3 ls`.
- **Files**: `infra/cfn/nested/090-bootstrap-bucket.yaml`.

### T-1.1 [p0] [infra] Network nested stack
Phase 1 · infra · p0 · deps: T-1.0
- **What**: `010-network.yaml` — VPC `10.0.0.0/16`, public subnet `10.0.1.0/24` in `ap-south-1a`, IGW, route table, security group.
- **AC**: `cfn-lint` clean; SG ingress 443/80 from `0.0.0.0/0`, 22 from `OperatorIpCidr`; egress 443 to `0.0.0.0/0`.
- **Files**: `infra/cfn/nested/010-network.yaml`.

### T-1.2 [p0] [infra] Secrets nested stack
Phase 1 · infra · p0 · deps: T-1.0
- **What**: `020-secrets.yaml` — KMS CMK `alias/mem-mcp` (if not pre-existing — gated by parameter), SSM parameter placeholders for all `/mem-mcp/*` keys (per LLD §4.1, spec §4.8).
- **AC**: `cfn-lint` clean; placeholders created; KMS key has rotation enabled.
- **Files**: `infra/cfn/nested/020-secrets.yaml`.

### T-1.3 [p0] [infra] Storage nested stack (S3 backups)
Phase 1 · infra · p0 · deps: T-1.2
- **What**: `030-storage.yaml` — S3 bucket `mem-mcp-backups-${Acct}-aps1` with versioning, SSE-KMS, BPA-all, deny-non-TLS, deny-non-KMS-uploads, lifecycle (Standard 30d → Glacier IR 365d → expire by `BackupRetentionDays`).
- **AC**: `cfn-lint` clean; bucket policy denies non-TLS PutObject.
- **Files**: `infra/cfn/nested/030-storage.yaml`.

### T-1.4 [p0] [infra] Identity nested stack (Cognito)
Phase 1 · infra · p0 · deps: T-1.2, T-0.6
- **What**: `040-identity.yaml` — `AWS::Cognito::UserPool`, custom domain pointing at `memauth.dheemantech.in` (using `UsEast1CertArn`), resource server `mem-mcp-api` with custom scopes `memory.read|memory.write|memory.admin|account.manage`, Google IdP from SSM secrets, web app client (confidential, PKCE).
- **AC**: `cfn-lint` clean; Hosted UI loads at `memauth.dheemantech.in`; resource server scopes visible in token customization.
- **Files**: `infra/cfn/nested/040-identity.yaml`.

### T-1.5 [p0] [infra] Lambda PreSignUp nested stack (SAM)
Phase 1 · infra · p0 · deps: T-1.4
- **What**: `050-lambda-presignup.yaml` (per LLD §2.5) — SAM `AWS::Serverless::Function`, ARM64, Python 3.12, env vars from SSM, Cognito invoke permission.
- **AC**: `sam build` clean; function deployable; Cognito trigger wired to user pool.
- **Files**: `infra/cfn/nested/050-lambda-presignup.yaml`, `lambdas/presignup/handler.py` (placeholder for T-4.8).

### T-1.6 [p0] [infra] Compute nested stack
Phase 1 · infra · p0 · deps: T-1.1, T-1.2, T-1.3
- **What**: `060-compute.yaml` — IAM instance profile (`mem-mcp-instance-role` with permissions per spec §4.9), EC2 t4g.medium ARM64 Ubuntu 24.04, EBS gp3 30GB encrypted, Elastic IP, EBS snapshot lifecycle (daily, 7d).
- **AC**: `cfn-lint` clean; instance reaches `running`; EIP attached; instance role can `bedrock:InvokeModel`.
- **Files**: `infra/cfn/nested/060-compute.yaml`.

### T-1.7 [p0] [infra] DNS nested stack
Phase 1 · infra · p0 · deps: T-1.6, T-1.4
- **What**: `070-dns.yaml` — A record `memsys.dheemantech.in` → EIP, A record `memapp.dheemantech.in` → EIP, ALIAS record `memauth.dheemantech.in` → Cognito custom domain.
- **AC**: `dig memsys.dheemantech.in` and `dig memapp.dheemantech.in` resolve to EIP.
- **Files**: `infra/cfn/nested/070-dns.yaml`.

### T-1.8 [p0] [infra] Observability nested stack
Phase 1 · infra · p0 · deps: T-1.6
- **What**: `080-observability.yaml` — CloudWatch log groups (`/mem-mcp/app`, `/mem-mcp/web`, `/mem-mcp/lambda/presignup`, `/mem-mcp/audit`), SNS topic `mem-mcp-ops` subscribed to `OperatorEmail`, dashboard `mem-mcp-overview`. Alarms scaffolded but actual metric filters added in T-9.6.
- **AC**: log groups created with retention; SNS subscription confirmed; dashboard renders.
- **Files**: `infra/cfn/nested/080-observability.yaml`.

### T-1.9 [p0] [infra] us-east-1 ACM cert stack
Phase 1 · infra · p0 · deps: T-0.1
- **What**: `infra/cfn/us-east-1/cert.yaml` — `AWS::CertificateManager::Certificate` for `memauth.dheemantech.in`, DNS validation via Route 53.
- **AC**: cert validated.
- **Files**: `infra/cfn/us-east-1/cert.yaml`.

### T-1.10 [p0] [infra] Root stack composition
Phase 1 · infra · p0 · deps: T-1.1..T-1.9
- **What**: `infra/cfn/root.yaml` orchestrates nested stacks via `AWS::CloudFormation::Stack`; passes outputs forward; writes critical outputs to SSM as `String` parameters.
- **AC**: `sam deploy` succeeds end-to-end.
- **Files**: `infra/cfn/root.yaml`, `infra/cfn/samconfig.toml`, `infra/cfn/parameters/prod.json`.

### T-1.11 [p0] [infra] cloud-init user-data
Phase 1 · infra · p0 · deps: T-1.10
- **What**: `infra/cloud-init/user-data.yaml` — installs Caddy, Postgres 16 + pgvector + pg_trgm, Python 3.12, Node 20, awscli, CloudWatch agent, fail2ban, unattended-upgrades; clones repo; calls `bootstrap.sh`.
- **AC**: VM reaches `/healthz` (stub) → 200.
- **Files**: `infra/cloud-init/user-data.yaml`.

### T-1.12 [p0] [infra] Destroy script
Phase 1 · infra · p0 · deps: T-1.10
- **What**: `deploy/scripts/destroy.sh` per LLD §3.2 (11 idempotent steps incl. safety gate, drain Cognito users/clients, empty buckets, delete stacks, KMS schedule deletion, orphan check).
- **AC**: dry-run against empty account exits 0; against running stack walks through all steps; orphan check returns clean.
- **Files**: `deploy/scripts/destroy.sh`, `deploy/scripts/verify_destroy.sh`.

### T-1.13 [p1] [infra] cfn-lint in CI
Phase 1 · infra · p1 · deps: T-1.10
- **What**: GitHub Actions job runs `cfn-lint infra/cfn/**/*.yaml` and `cfn-lint infra/cfn/us-east-1/cert.yaml`.
- **AC**: workflow lights green; intentional bad-input test fails as expected.
- **Files**: `.github/workflows/infra-lint.yml`.

---

## Phase 2 — Database

### T-2.1 [p0] [infra] Alembic project skeleton
Phase 2 · infra · p0 · deps: T-1.11
- **What**: `alembic.ini`, `alembic/env.py` configured to read `MEM_MCP_DB_MAINT_DSN` from `mem_mcp.config`.
- **AC**: `alembic upgrade head` no-ops on a fresh empty DB.
- **Files**: `alembic.ini`, `alembic/env.py`.

### T-2.2 [p0] [infra] Migration `0001_initial_schema`
Phase 2 · infra · p0 · deps: T-2.1
- **What**: Encode spec §8.3 DDL via raw SQL in `op.execute()`. Apply LLD §5.1 deltas (`tenant_identities.provider` CHECK `IN ('google','cognito')`; keep `oauth_consents` DDL with comment `-- v2-ready, unused in v1`). Includes RLS policies on `memories` and `tenant_daily_usage`. Per-table grants for `mem_app`/`mem_maint` at end.
- **AC**: all tables, indexes, RLS policies present; `\dp memories` shows correct grants.
- **Files**: `alembic/versions/0001_initial_schema.py`.

### T-2.3 [p0] [infra] Migration `0002_seed_allowed_software`
Phase 2 · infra · p0 · deps: T-2.2
- **What**: INSERT seed rows into `allowed_software` per spec §8.4: `claude-code`, `claude-ai`, `chatgpt` status='allowed'; `cursor`, `perplexity` status='blocked'.
- **AC**: rows present.
- **Files**: `alembic/versions/0002_seed_allowed_software.py`.

### T-2.4 [p0] [infra] Roles & grants script
Phase 2 · infra · p0 · deps: T-2.2
- **What**: `deploy/postgres/init_roles.sql` per LLD §5.3 (creates `mem_app`, `mem_maint BYPASSRLS`, owner DB).
- **AC**: roles exist; `mem_maint` is BYPASSRLS; default privileges set.
- **Files**: `deploy/postgres/init_roles.sql`.

### T-2.5 [p0] [tests] RLS smoke test (manual + scripted)
Phase 2 · tests · p0 · deps: T-2.4
- **What**: A small psql script that connects as `mem_app` without setting `app.current_tenant_id`, runs `SELECT * FROM memories LIMIT 1`, asserts 0 rows.
- **AC**: script returns 0 rows; documented in `docs/runbooks/db_smoke.md`.
- **Files**: `deploy/postgres/smoke_rls.sql`, `docs/runbooks/db_smoke.md`.

### T-2.6 [p1] [infra] postgresql.conf fragment
Phase 2 · infra · p1 · deps: T-1.11
- **What**: `deploy/postgres/postgresql.conf.fragment` with tuning per spec §8.1; `pg_hba.conf` enforces scram-sha-256 from localhost only.
- **AC**: applied via cloud-init; Postgres reload succeeds; `pg_settings` shows the values.
- **Files**: `deploy/postgres/postgresql.conf.fragment`, `deploy/postgres/pg_hba.conf`.

---

## Phase 3 — App skeleton

### T-3.1 [p0] [infra] Poetry project + pyproject
Phase 3 · infra · p0 · deps: —
- **What**: `pyproject.toml` with deps: fastapi, uvicorn, asyncpg, pydantic, pydantic-settings, structlog, boto3, python-jose[cryptography], httpx, tenacity, aiocache. Dev deps: ruff, mypy, pytest, pytest-asyncio, hypothesis, cfn-lint.
- **AC**: `poetry install` clean; `python -c "import mem_mcp"` succeeds (with empty `__init__.py`).
- **Files**: `pyproject.toml`, `poetry.lock`, `.python-version`, `src/mem_mcp/__init__.py`.

### T-3.2 [p0] [auth] Config + SSM loader
Phase 3 · auth · p0 · deps: T-3.1
- **What**: `mem_mcp/config.py` per LLD §4.1 (Pydantic Settings, single SSM `GetParametersByPath` at startup).
- **AC**: unit test: env-only loads; SSM-only loads; mixed loads.
- **Files**: `src/mem_mcp/config.py`, `tests/unit/test_config.py`.

### T-3.3 [p0] [infra] structlog logging setup
Phase 3 · infra · p0 · deps: T-3.2
- **What**: `mem_mcp/logging_setup.py` — JSON to stdout; redact filter for `Authorization`, `password`, `token`, `secret`, `cookie`, `mem_session`, `gpg_passphrase`. Bind context `request_id`, `tenant_id`, `client_id`.
- **AC**: unit test asserts redaction; emitted line is valid JSON.
- **Files**: `src/mem_mcp/logging_setup.py`, `tests/unit/test_logging_redact.py`.

### T-3.4 [p0] [infra] DB pool + tenant_tx + system_tx
Phase 3 · infra · p0 · deps: T-3.2
- **What**: `mem_mcp/db/pool.py`, `mem_mcp/db/tenant_tx.py` per LLD §4.2.
- **AC**: integration test verifies `app.current_tenant_id` is `LOCAL` (does not leak across pool acquires).
- **Files**: `src/mem_mcp/db/pool.py`, `src/mem_mcp/db/tenant_tx.py`, `tests/integration/test_tenant_tx.py`.

### T-3.5 [p0] [mcp] FastAPI entry + healthz/readyz
Phase 3 · mcp · p0 · deps: T-3.4
- **What**: `mem_mcp/main.py` — FastAPI app, lifespan opens DB pool, `/healthz` returns `{ok:true}`, `/readyz` checks DB + Bedrock + Cognito JWKS.
- **AC**: `pytest tests/integration/test_healthz.py` passes; both endpoints 200 in healthy state, 503 when a dependency mock is down.
- **Files**: `src/mem_mcp/main.py`, `tests/integration/test_healthz.py`.

### T-3.6 [p0] [infra] systemd unit `mem-mcp.service`
Phase 3 · infra · p0 · deps: T-3.5
- **What**: `deploy/systemd/mem-mcp.service` per spec Appendix F; hardening flags.
- **AC**: `systemctl restart mem-mcp` works on staging VM; `systemctl status` shows active.
- **Files**: `deploy/systemd/mem-mcp.service`.

### T-3.7 [p0] [infra] Caddyfile
Phase 3 · infra · p0 · deps: T-3.6, T-1.7
- **What**: `deploy/Caddyfile` per LLD §8.2 (memsys, memapp with path-routing of `/api/web/*` `/auth/*` to :8080).
- **AC**: `curl https://memsys.dheemantech.in/healthz` returns 200 with valid cert; `curl https://memapp.dheemantech.in/api/web/me` reaches FastAPI (returns 401 due to missing session — proves routing).
- **Files**: `deploy/Caddyfile`.

---

## Phase 4 — Authentication & DCR

### T-4.1 [p0] [auth] JWKS fetch + cache
Phase 4 · auth · p0 · deps: T-3.4
- **What**: `mem_mcp/auth/jwks.py` — async fetch from Cognito, in-memory cache 1h TTL, refresh on `kid` miss.
- **AC**: unit test with synthetic keyset; verifies kid-miss refresh.
- **Files**: `src/mem_mcp/auth/jwks.py`, `tests/unit/test_jwks.py`.

### T-4.2 [p0] [auth] JWT validator
Phase 4 · auth · p0 · deps: T-4.1
- **What**: `mem_mcp/auth/jwt_validator.py` per LLD §4.3.1.
- **AC**: tests for: valid signature; expired; bad signature; wrong iss; wrong aud; missing claims. All raise `JwtError(code=...)` with correct code.
- **Files**: `src/mem_mcp/auth/jwt_validator.py`, `tests/unit/test_jwt_validator.py`.

### T-4.3 [p0] [auth] Bearer middleware
Phase 4 · auth · p0 · deps: T-4.2
- **What**: `mem_mcp/auth/middleware.py` per LLD §4.3.2.
- **AC**: integration tests for 401 (no token), 401 (invalid token), 403 (suspended/pending_deletion), success path with `tenant_ctx` populated.
- **Files**: `src/mem_mcp/auth/middleware.py`, `tests/integration/test_bearer_middleware.py`.

### T-4.4 [p0] [auth] Well-known endpoints
Phase 4 · auth · p0 · deps: T-3.5
- **What**: `mem_mcp/auth/well_known.py` — `/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server` returning shapes per spec §6.3, §6.4. AS metadata points at Cognito directly (no consent shim in v1).
- **AC**: response JSON schema-validated; URLs match `memauth.dheemantech.in/oauth2/...`.
- **Files**: `src/mem_mcp/auth/well_known.py`, `tests/integration/test_well_known.py`.

### T-4.5 [p0] [auth] DCR endpoint POST `/oauth/register`
Phase 4 · auth · p0 · deps: T-4.4
- **What**: `mem_mcp/auth/dcr.py` per LLD §4.3.3. Validation, allowlist check, per-IP rate limit, Cognito `CreateUserPoolClient` with `SupportedIdentityProviders=['Google']`, INSERT `oauth_clients`, mint `registration_access_token` (sha256 stored).
- **AC**: integration test against mocked Cognito (moto) covering: success, unknown software_id (403), blocked software_id (403), invalid redirect_uri (400), rate-limit (429).
- **Files**: `src/mem_mcp/auth/dcr.py`, `tests/integration/test_dcr.py`.

### T-4.6 [p1] [auth] DCR admin endpoints (GET/DELETE)
Phase 4 · auth · p1 · deps: T-4.5
- **What**: `mem_mcp/auth/dcr_admin.py` — GET/DELETE `/oauth/register/{client_id}` with `registration_access_token` auth.
- **AC**: tests for valid/invalid token, 404 unknown client, DELETE side effects.
- **Files**: `src/mem_mcp/auth/dcr_admin.py`, `tests/integration/test_dcr_admin.py`.

### T-4.7 [p0] [auth] Internal invite check endpoint
Phase 4 · auth · p0 · deps: T-3.4
- **What**: `mem_mcp/auth/internal_invite.py` — `POST /internal/check_invite` HMAC-protected. Returns `{decision: allow|deny, reason: invited|not_invited}` (v1 simplified — no email-collision branch).
- **AC**: tests for HMAC validation, allow/deny decisions.
- **Files**: `src/mem_mcp/auth/internal_invite.py`, `tests/integration/test_internal_invite.py`.

### T-4.8 [p0] [auth] PreSignUp Lambda handler
Phase 4 · auth · p0 · deps: T-4.7, T-1.5
- **What**: `lambdas/presignup/handler.py` calls `/internal/check_invite` with HMAC; allows/denies per response. Python 3.12. Logs structured JSON.
- **AC**: integration test against mocked endpoint; live test against staging Cognito.
- **Files**: `lambdas/presignup/handler.py`, `lambdas/presignup/requirements.txt`, `lambdas/presignup/tests/test_handler.py`.

### T-4.9 [p1] [auth] DCR cleanup job
Phase 4 · auth · p1 · deps: T-4.5
- **What**: `mem_mcp/jobs/cleanup_clients.py` — daily systemd timer per FR-6.5.10. Deletes Cognito clients never used > 24h or unused > 90d.
- **AC**: unit test of decision logic; integration test against moto.
- **Files**: `src/mem_mcp/jobs/cleanup_clients.py`, `deploy/systemd/mem-mcp-cleanup-clients.{service,timer}`, `tests/unit/test_cleanup_clients.py`.

### T-4.10 [p0] [tests] Live OAuth integration test (staging)
Phase 4 · tests · p0 · deps: T-4.5..T-4.8
- **What**: A pytest test marked `@pytest.mark.live_aws` that runs the full DCR + authorize + token + MCP-call cycle against a staging Cognito.
- **AC**: test passes manually; documented in `tests/integration/README.md`.
- **Files**: `tests/integration/test_oauth_live.py`, `tests/integration/README.md`.

### T-4.11 [p1] [auth] `seed_invite.py` CLI
Phase 4 · auth · p1 · deps: T-4.7
- **What**: `deploy/scripts/seed_invite.py` — small CLI that connects as `mem_maint` and INSERT/UPDATE rows in `invited_emails`. Used by operator to add beta users.
- **AC**: CLI exists; `--help` output sensible; integration test inserts a row.
- **Files**: `deploy/scripts/seed_invite.py`, `tests/integration/test_seed_invite.py`.

---

## Phase 5 — MCP transport + first tools

### T-5.1 [p0] [mcp] Streamable HTTP `/mcp` handler
Phase 5 · mcp · p0 · deps: T-4.3
- **What**: `mem_mcp/mcp/transport.py` — POST `/mcp` accepts JSON-RPC 2.0; SSE on demand; integrates Bearer middleware.
- **AC**: minimal `tools/list` returns the registered tools; unauthenticated returns 401 with WWW-Authenticate header.
- **Files**: `src/mem_mcp/mcp/transport.py`, `tests/integration/test_mcp_transport.py`.

### T-5.2 [p0] [mcp] Tool registry + dispatch
Phase 5 · mcp · p0 · deps: T-5.1
- **What**: `mem_mcp/mcp/registry.py` — register tools by name, lookup by JSON-RPC method, per-tool scope check, per-tool input/output validation via Pydantic.
- **AC**: dispatch tests for: unknown tool, scope mismatch, invalid input, success.
- **Files**: `src/mem_mcp/mcp/registry.py`, `src/mem_mcp/mcp/tools/_base.py`, `tests/integration/test_mcp_dispatch.py`.

### T-5.3 [p0] [mcp] JSON-RPC error mapping
Phase 5 · mcp · p0 · deps: T-5.2
- **What**: `mem_mcp/mcp/errors.py` per spec §9.4. `to_jsonrpc_error(exc)` for typed exceptions (`JwtError`, `QuotaError`, `EmbeddingError`, `ValidationError`, etc.).
- **AC**: unit test per error type.
- **Files**: `src/mem_mcp/mcp/errors.py`, `tests/unit/test_jsonrpc_errors.py`.

### T-5.4 [p0] [memory] Bedrock Titan v2 client
Phase 5 · memory · p0 · deps: T-3.2
- **What**: `mem_mcp/embeddings/bedrock.py` per LLD §4.4. Tenacity backoff; `asyncio.to_thread` over boto3.
- **AC**: integration test against live Bedrock (`@pytest.mark.live_aws`); unit test against mocked boto3.
- **Files**: `src/mem_mcp/embeddings/bedrock.py`, `tests/unit/test_bedrock_client.py`, `tests/integration/test_bedrock_live.py`.

### T-5.5 [p0] [memory] `normalize_for_hash` + `hash_content`
Phase 5 · memory · p0 · deps: T-3.1
- **What**: `mem_mcp/memory/normalize.py` per LLD §4.5.
- **AC**: property test (Hypothesis): whitespace/casing/NFKC variants produce same hash.
- **Files**: `src/mem_mcp/memory/normalize.py`, `tests/unit/test_normalize.py`.

### T-5.6 [p0] [memory] Dedupe (`check_dup`)
Phase 5 · memory · p0 · deps: T-5.5
- **What**: `mem_mcp/memory/dedupe.py` per LLD §4.5.2.
- **AC**: integration tests: hash hit, embedding hit (sim>0.95), no hit, cross-tenant invisibility.
- **Files**: `src/mem_mcp/memory/dedupe.py`, `tests/integration/test_dedupe.py`.

### T-5.7 [p0] [memory] Hybrid search SQL + Python wrapper
Phase 5 · memory · p0 · deps: T-3.4
- **What**: `mem_mcp/memory/hybrid_query.py` — SQL from spec §10.3 (verbatim) + `hybrid_search(conn, params)` wrapper.
- **AC**: integration test on a curated 50-memory dataset asserts expected ordering for several queries.
- **Files**: `src/mem_mcp/memory/hybrid_query.py`, `tests/integration/test_hybrid_query.py`, `tests/integration/fixtures/curated_memories.json`.

### T-5.8 [p0] [memory] Recency lambda config + per-type defaults
Phase 5 · memory · p0 · deps: T-5.7
- **What**: `mem_mcp/config.py` extension: `RECENCY_LAMBDA_BY_TYPE` constant per spec §10.4. Search uses this default unless overridden.
- **AC**: unit test of the lookup function.
- **Files**: `src/mem_mcp/memory/recency.py`, `tests/unit/test_recency.py`.

### T-5.9 [p0] [memory] Tool: `memory.write`
Phase 5 · memory · p0 · deps: T-5.4..T-5.8
- **What**: `mem_mcp/mcp/tools/write.py` per spec §9.3.1 + LLD §4.6.1. Includes Pydantic models, dedupe, supersedes branch, audit log, quota increment, full transaction.
- **AC**: integration tests cover: insert new; hash dedupe; embedding dedupe; supersedes path; quota_exceeded error; embedding_unavailable error.
- **Files**: `src/mem_mcp/mcp/tools/write.py`, `tests/integration/test_tool_write.py`.

### T-5.10 [p0] [memory] Tool: `memory.search`
Phase 5 · memory · p0 · deps: T-5.7..T-5.9
- **What**: `mem_mcp/mcp/tools/search.py` per spec §9.3.2.
- **AC**: integration tests on curated dataset; type filter; tag filter; date filter.
- **Files**: `src/mem_mcp/mcp/tools/search.py`, `tests/integration/test_tool_search.py`.

### T-5.11 [p0] [memory] Tool: `memory.get`
Phase 5 · memory · p0 · deps: T-5.9
- **What**: `mem_mcp/mcp/tools/get.py` per spec §9.3.3.
- **AC**: tests: hit; not found; cross-tenant 404; with `include_history`.
- **Files**: `src/mem_mcp/mcp/tools/get.py`, `tests/integration/test_tool_get.py`.

### T-5.12 [p0] [auth] Audit logger
Phase 5 · auth · p0 · deps: T-3.4
- **What**: `mem_mcp/audit/logger.py` per LLD §4.9. Audit insert on the same connection as the operation.
- **AC**: tests verify a row is written for success, denied, error; rolls back with the parent tx.
- **Files**: `src/mem_mcp/audit/logger.py`, `tests/integration/test_audit.py`.

### T-5.13 [p0] [tests] First Claude Code end-to-end (manual)
Phase 5 · tests · p0 · deps: T-5.9..T-5.11
- **What**: Manual: `claude mcp add --transport http mem-mcp https://memsys.dheemantech.in/mcp` against staging; complete OAuth; write & retrieve a memory.
- **AC**: smoke success documented in `docs/runbooks/manual_e2e.md`.
- **Files**: `docs/runbooks/manual_e2e.md`.

---

## Phase 6 — Tenant isolation hardening (gating)

> No external user invited until this phase passes.

### T-6.1 [p0] [tests] Tenant-scope linter
Phase 6 · tests · p0 · deps: Phase 5
- **What**: `tools/lint_tenant_scope.py` per LLD §11. Runs in pre-commit + CI.
- **AC**: planted regression in a test file is caught; clean code passes.
- **Files**: `tools/lint_tenant_scope.py`, `.pre-commit-config.yaml`, `.github/workflows/lint.yml`.

### T-6.2 [p0] [tests] Two-tenant fixtures
Phase 6 · tests · p0 · deps: Phase 5
- **What**: `tests/conftest.py` fixtures `setup_two_tenants`, `jwt_factory`, `mcp_client` per LLD §11.
- **AC**: importable from any test.
- **Files**: `tests/conftest.py`.

### T-6.3 [p0] [tests] Cross-tenant search isolation
Phase 6 · tests · p0 · deps: T-6.2
- **What**: Per spec §18.3 S-1.
- **AC**: passes.
- **Files**: `tests/security/test_cross_tenant_search.py`.

### T-6.4 [p0] [tests] SQLi probes in tags/query/type
Phase 6 · tests · p0 · deps: T-6.2
- **What**: Per spec S-2 (parametrized).
- **AC**: passes.
- **Files**: `tests/security/test_sqli_probes.py`.

### T-6.5 [p0] [tests] RLS fail-closed without SET LOCAL
Phase 6 · tests · p0 · deps: T-2.5
- **What**: Per spec S-3.
- **AC**: passes.
- **Files**: `tests/security/test_rls_failclosed.py`.

### T-6.6 [p0] [tests] Pool tenancy isolation under concurrency
Phase 6 · tests · p0 · deps: T-3.4
- **What**: Per spec S-4.
- **AC**: passes.
- **Files**: `tests/security/test_pool_isolation.py`.

### T-6.7 [p0] [tests] Scope enforcement
Phase 6 · tests · p0 · deps: T-5.2
- **What**: token without `memory.write` rejected on `memory.write`.
- **AC**: passes.
- **Files**: `tests/security/test_scope_enforcement.py`.

### T-6.8 [p1] [tests] Token reuse detection
Phase 6 · tests · p1 · deps: Phase 4
- **What**: Cognito's revocation behavior verified end-to-end (live test).
- **AC**: passes.
- **Files**: `tests/security/test_token_reuse.py`.

### T-6.9 [p0] [tests] Tenant status enforcement
Phase 6 · tests · p0 · deps: T-4.3
- **What**: suspended / pending_deletion tenant cannot call `/mcp`.
- **AC**: passes.
- **Files**: `tests/security/test_tenant_status.py`.

### T-6.10 [p1] [ops] Synthetic alarm: nightly security suite
Phase 6 · ops · p1 · deps: T-6.3..T-6.7
- **What**: GitHub Actions nightly runs `pytest tests/security` against staging; on fail, emits CloudWatch metric → SNS alarm.
- **AC**: induced failure triggers alarm.
- **Files**: `.github/workflows/security-nightly.yml`.

### T-6.11 [p0] [docs] Manual review sign-off
Phase 6 · docs · p0 · deps: T-6.3..T-6.10
- **What**: PR comment from operator reviewing the security tests themselves are correct (not just passing).
- **AC**: signed comment in PR.

---

## Phase 7 — Remaining tools, lifecycle, retention

### T-7.1 [p0] [memory] Tool: `memory.list`
Phase 7 · memory · p0 · deps: T-5.10
- **What**: spec §9.3.4 with cursor pagination.
- **AC**: tests for filter combinations; cursor stability.
- **Files**: `src/mem_mcp/mcp/tools/list.py`, `tests/integration/test_tool_list.py`.

### T-7.2 [p0] [memory] Tool: `memory.update` (versioning)
Phase 7 · memory · p0 · deps: T-5.9
- **What**: spec §9.3.5; in-place vs new-version per type.
- **AC**: tests covering note (in-place), decision (new version), type promotion, re-embed on content change.
- **Files**: `src/mem_mcp/mcp/tools/update.py`, `src/mem_mcp/memory/versioning.py`, `tests/integration/test_tool_update.py`.

### T-7.3 [p0] [memory] Tool: `memory.delete`
Phase 7 · memory · p0 · deps: T-5.9
- **What**: spec §9.3.6 (soft delete).
- **AC**: tests; cascade flag requires `memory.admin` scope.
- **Files**: `src/mem_mcp/mcp/tools/delete.py`, `tests/integration/test_tool_delete.py`.

### T-7.4 [p0] [memory] Tool: `memory.undelete`
Phase 7 · memory · p0 · deps: T-7.3
- **What**: spec §9.3.7 (≤30d window).
- **AC**: tests including grace-period boundary; conflict if a current sibling exists in versioned chain.
- **Files**: `src/mem_mcp/mcp/tools/undelete.py`, `tests/integration/test_tool_undelete.py`.

### T-7.5 [p0] [memory] Tool: `memory.supersede`
Phase 7 · memory · p0 · deps: T-7.2
- **What**: spec §9.3.8.
- **AC**: tests; only versioned types accepted; only same-type pairs.
- **Files**: `src/mem_mcp/mcp/tools/supersede.py`, `tests/integration/test_tool_supersede.py`.

### T-7.6 [p0] [memory] Tool: `memory.export`
Phase 7 · memory · p0 · deps: T-5.9
- **What**: spec §9.3.9; requires `memory.admin`. Streams JSON.
- **AC**: tests on multi-MB export; tenant isolation.
- **Files**: `src/mem_mcp/mcp/tools/export.py`, `tests/integration/test_tool_export.py`.

### T-7.7 [p0] [memory] Tool: `memory.stats`
Phase 7 · memory · p0 · deps: T-5.9
- **What**: spec §9.3.10.
- **AC**: tests verify counts, top tags, quota fields.
- **Files**: `src/mem_mcp/mcp/tools/stats.py`, `tests/integration/test_tool_stats.py`.

### T-7.8 [p1] [memory] Tool: `memory.feedback`
Phase 7 · memory · p1 · deps: T-3.4
- **What**: spec §9.3.11; INSERT into `feedback`.
- **AC**: persists; daily summary email scheduled (T-9.6).
- **Files**: `src/mem_mcp/mcp/tools/feedback.py`, `tests/integration/test_tool_feedback.py`.

### T-7.9 [p0] [memory] Quota enforcer
Phase 7 · memory · p0 · deps: T-5.9, T-5.10
- **What**: `mem_mcp/quotas/enforcer.py` per LLD §4.7.
- **AC**: per-minute (token bucket) and per-day (table) tests; structured JSON-RPC error.
- **Files**: `src/mem_mcp/quotas/enforcer.py`, `src/mem_mcp/quotas/tiers.py`, `src/mem_mcp/quotas/usage.py`, `src/mem_mcp/ratelimit/token_bucket.py`, `tests/integration/test_quotas.py`.

### T-7.10 [p0] [auth] Identity link start + complete
Phase 7 · auth · p0 · deps: T-4.7, T-3.4
- **What**: `mem_mcp/identity/linking.py` per LLD §4.10.1 + sequence diagram §6.2.
- **AC**: tests cover happy path; tampered HMAC; expired state; cookie/state mismatch; cross-session attack (different web session).
- **Files**: `src/mem_mcp/identity/linking.py`, `tests/integration/test_link_flow.py`, `tests/security/test_link_attacks.py`.

### T-7.11 [p0] [auth] Identity unlink + promote-primary
Phase 7 · auth · p0 · deps: T-7.10
- **What**: `mem_mcp/identity/unlinking.py`.
- **AC**: tests: cannot unlink last identity; primary unlink requires another to be promoted; AdminDeleteUser called.
- **Files**: `src/mem_mcp/identity/unlinking.py`, `tests/integration/test_unlinking.py`.

### T-7.12 [p0] [auth] Account closure flow
Phase 7 · auth · p0 · deps: T-3.4
- **What**: `mem_mcp/identity/lifecycle.py` per spec §7.7 + LLD §6.6. `AdminUserGlobalSignOut` per `cognito_sub`. Cancel within 24h.
- **AC**: tests covering cancel and finalize paths.
- **Files**: `src/mem_mcp/identity/lifecycle.py`, `src/mem_mcp/jobs/retention_deletion.py`, `tests/integration/test_account_closure.py`.

### T-7.13 [p0] [web] Connected applications revoke
Phase 7 · web · p0 · deps: T-7.12
- **What**: web API `DELETE /api/web/clients/{id}` marks `oauth_clients.disabled=true` + Cognito `DeleteUserPoolClient`.
- **AC**: subsequent JWT validations for that client fail at middleware (401 due to deleted Cognito client).
- **Files**: `src/mem_mcp/web/handlers/clients.py`, `tests/integration/test_client_revoke.py`.

### T-7.14 [p0] [ops] Retention jobs (memories, tokens, audit, deletion)
Phase 7 · ops · p0 · deps: Phase 5, T-7.12
- **What**: `mem_mcp/jobs/retention_*.py` per LLD §4.12 + spec §14.3. systemd timers.
- **AC**: dry-run tests + real run on staging.
- **Files**: `src/mem_mcp/jobs/retention_memories.py`, `retention_tokens.py`, `retention_audit.py`, `retention_deletion.py`, `_runner.py`, `deploy/systemd/mem-mcp-retention-*.{service,timer}`, `tests/integration/test_retention.py`.

### T-7.15 [p1] [ops] Audit anonymization (90d post-deletion)
Phase 7 · ops · p1 · deps: T-7.14
- **What**: `retention_audit.py` anonymizes `audit_log.tenant_id` to NULL and redacts PII in `details` for tenants deleted > 90d.
- **AC**: test asserts before/after rows.
- **Files**: same as T-7.14 (extension).

---

## Phase 8 — Web app (Next.js)

### T-8.1 [p0] [web] Next.js scaffold
Phase 8 · web · p0 · deps: T-3.7
- **What**: `web/` — Next.js 15 App Router, TypeScript strict, Tailwind, shadcn-style components scaffold.
- **AC**: `pnpm build` succeeds; `pnpm dev` serves on :8081.
- **Files**: `web/package.json`, `web/tsconfig.json`, `web/next.config.mjs`, `web/tailwind.config.ts`, `web/app/layout.tsx`, `web/app/page.tsx`.

### T-8.2 [p0] [web] Cognito login + callback (FastAPI side)
Phase 8 · web · p0 · deps: T-7.10, T-8.1
- **What**: `mem_mcp/web/routes.py` — `/auth/login` (302 to Cognito with state+PKCE), `/auth/callback` (token exchange; first-signup vs link branch per LLD §4.10.1/4.10.2), `/auth/logout`. `mem_mcp/web/sessions.py` per LLD §4.11.1.
- **AC**: end-to-end login; session created; logout invalidates session.
- **Files**: `src/mem_mcp/web/routes.py`, `src/mem_mcp/web/sessions.py`, `tests/integration/test_web_auth.py`.

### T-8.3 [p0] [web] CSRF middleware
Phase 8 · web · p0 · deps: T-8.2
- **What**: `mem_mcp/web/csrf.py` per LLD §4.11.2.
- **AC**: POST without `X-CSRF-Token` → 403; with valid token → succeeds.
- **Files**: `src/mem_mcp/web/csrf.py`, `tests/security/test_csrf.py`.

### T-8.4 [p0] [web] `/welcome` page
Phase 8 · web · p0 · deps: T-8.2
- **What**: copy-paste cards for Claude Code/Claude.ai/ChatGPT install per spec §12.3.2.
- **AC**: visual review; cards copyable.
- **Files**: `web/app/welcome/page.tsx`.

### T-8.5 [p0] [web] `/dashboard` page + `/api/web/stats`
Phase 8 · web · p0 · deps: T-7.7, T-8.2
- **What**: stats + quota bars; backend `/api/web/stats`.
- **AC**: numbers match `memory.stats`.
- **Files**: `web/app/dashboard/page.tsx`, `src/mem_mcp/web/handlers/stats.py`.

### T-8.6 [p0] [web] `/memories` list + filter UI
Phase 8 · web · p0 · deps: T-7.1, T-8.2
- **What**: `web/app/memories/page.tsx`; backend `GET /api/web/memories` reuses `memory.list` semantics.
- **AC**: filters work; pagination cursor stable.
- **Files**: `web/app/memories/page.tsx`, `src/mem_mcp/web/handlers/memories.py`.

### T-8.7 [p0] [web] `/memories/[id]` detail + edit/delete/undelete + history
Phase 8 · web · p0 · deps: T-7.2..T-7.5, T-8.6
- **What**: `web/app/memories/[id]/page.tsx`; backend handlers wrap tools.
- **AC**: full CRUD via UI.
- **Files**: `web/app/memories/[id]/page.tsx`.

### T-8.8 [p0] [web] `/settings` profile + retention + closure CTA
Phase 8 · web · p0 · deps: T-7.12, T-8.2
- **What**: `web/app/settings/page.tsx`. Email read-only in v1 (LLD §0).
- **AC**: PATCH retention_days enforces validation 7–3650.
- **Files**: `web/app/settings/page.tsx`, `src/mem_mcp/web/handlers/tenant.py`.

### T-8.9 [p0] [web] `/settings/identities` page
Phase 8 · web · p0 · deps: T-7.10, T-7.11
- **What**: `web/app/settings/identities/page.tsx` — list, link start, unlink, promote.
- **AC**: full link/unlink flow tested.
- **Files**: `web/app/settings/identities/page.tsx`, `src/mem_mcp/web/handlers/identities.py`.

### T-8.10 [p0] [web] `/settings/applications` page
Phase 8 · web · p0 · deps: T-7.13
- **What**: `web/app/settings/applications/page.tsx` — list of `oauth_clients` for the tenant; revoke button.
- **AC**: revoke confirmed by Cognito.
- **Files**: `web/app/settings/applications/page.tsx`.

### T-8.11 [p1] [web] `/settings/feedback` page
Phase 8 · web · p1 · deps: T-7.8
- **What**: textarea + submit → `/api/web/feedback` → `feedback` table.
- **AC**: row persists.
- **Files**: `web/app/settings/feedback/page.tsx`, `src/mem_mcp/web/handlers/feedback.py`.

### T-8.12 [p0] [web] `/data/export` page
Phase 8 · web · p0 · deps: T-7.6
- **What**: streams JSON dump.
- **AC**: large export downloads cleanly; SHA256 in filename.
- **Files**: `web/app/data/export/page.tsx`, `src/mem_mcp/web/handlers/export.py`.

### T-8.13 [p0] [web] `/data/delete` flow
Phase 8 · web · p0 · deps: T-7.12
- **What**: multi-step confirmation, Cognito step-up; banner during 24h cancel window.
- **AC**: cancel + finalize tested.
- **Files**: `web/app/data/delete/page.tsx`.

### T-8.14 [p1] [web] `/skills` page
Phase 8 · web · p1 · deps: Phase 9 (T-9.1, T-9.2)
- **What**: static install instructions + downloads of `.skill` bundles.
- **AC**: visual.
- **Files**: `web/app/skills/page.tsx`.

### T-8.15 [p0] [web] Legal pages (privacy, terms)
Phase 8 · web · p0 · deps: T-8.1
- **What**: drafts to be reviewed by counsel before Google OAuth verification.
- **AC**: pages render.
- **Files**: `web/app/legal/privacy/page.tsx`, `web/app/legal/terms/page.tsx`, `docs/legal/PRIVACY.md`, `docs/legal/TERMS.md`.

### T-8.16 [p0] [web] CSP headers in Caddy
Phase 8 · web · p0 · deps: T-3.7
- **What**: Caddyfile adds CSP for `memapp.dheemantech.in` per spec §12.6.
- **AC**: testssl.sh / SecurityHeaders rates A+.
- **Files**: `deploy/Caddyfile`.

---

## Phase 9 — Skills, integration, beta-readiness

### T-9.1 [p0] [docs] `mem-capture` skill bundle
Phase 9 · docs · p0 · deps: T-5.9
- **What**: `skills/mem-capture/SKILL.md` per spec §13.2 + `meta.yaml` with `connector_url: https://memsys.dheemantech.in/mcp`.
- **AC**: works in Claude Code against staging.
- **Files**: `skills/mem-capture/SKILL.md`, `skills/mem-capture/meta.yaml`.

### T-9.2 [p0] [docs] `mem-recall` skill bundle
Phase 9 · docs · p0 · deps: T-5.10
- **What**: `skills/mem-recall/SKILL.md` per spec §13.3.
- **AC**: works in Claude Code.
- **Files**: `skills/mem-recall/SKILL.md`, `skills/mem-recall/meta.yaml`.

### T-9.3 [p1] [docs] Claude.ai project instructions template
Phase 9 · docs · p1 · deps: T-9.1, T-9.2
- **What**: copy-paste block per spec §13.4.
- **AC**: tested with 3 invitees.
- **Files**: `docs/integration/claude_ai_instructions.md`.

### T-9.4 [p1] [docs] ChatGPT custom GPT instructions template
Phase 9 · docs · p1 · deps: T-9.1, T-9.2
- **What**: per spec §13.5.
- **AC**: same.
- **Files**: `docs/integration/chatgpt_instructions.md`.

### T-9.5 [p0] [tests] Eval harness for retrieval
Phase 9 · tests · p0 · deps: T-5.10
- **What**: `eval/retrieval/dataset.jsonl` (20 hand-curated triples) + `run_eval.py`. Outputs JSON report with top-3 hit-rate, MRR.
- **AC**: harness runs; baseline numbers recorded in `eval/retrieval/baseline.json`.
- **Files**: `eval/retrieval/dataset.jsonl`, `eval/retrieval/run_eval.py`, `eval/retrieval/baseline.json`, `eval/README.md`.

### T-9.6 [p0] [ops] CloudWatch alarms full set
Phase 9 · ops · p0 · deps: T-1.8, Phase 5..7
- **What**: extend `080-observability.yaml` with all alarms in spec §14.4 + log metric filters.
- **AC**: synthetic failure triggers each alarm.
- **Files**: `infra/cfn/nested/080-observability.yaml`.

### T-9.7 [p0] [ops] Backup script + restore drill
Phase 9 · ops · p0 · deps: T-1.6
- **What**: `deploy/scripts/pg_dump_to_s3.sh` (per LLD §10.3) + `restore_from_s3.sh`. systemd timer 02:30 IST.
- **AC**: nightly run produces object; restore on a fresh VM smoke-tested.
- **Files**: `deploy/scripts/pg_dump_to_s3.sh`, `deploy/scripts/restore_from_s3.sh`, `deploy/systemd/mem-mcp-backup.{service,timer}`.

### T-9.8 [p0] [docs] Operator runbooks
Phase 9 · docs · p0 · deps: Phase 5..8
- **What**: `docs/runbooks/` — `add_beta_user.md`, `suspend_tenant.md`, `dpdp_export_request.md`, `dpdp_delete_request.md`, `restore_from_backup.md`, `rotate_jwt_keys.md`, `investigate_token_reuse.md`, `add_allowed_software.md`, `lockout_recovery.md`, `tenant_merge.md`, `wind_down.md`, `destroy_partial.md`.
- **AC**: each exercised once; PR signed off.
- **Files**: `docs/runbooks/*.md`.

### T-9.9 [p1] [tests] Load test
Phase 9 · tests · p1 · deps: Phase 5
- **What**: 100 concurrent searches, 10 active tenants. Record p50/p95/p99 (excluding Bedrock).
- **AC**: p95 < 250ms.
- **Files**: `tests/load/locustfile.py`, `docs/load_test_results.md`.

### T-9.10 [p0] [docs] INVITE.md for beta invitees
Phase 9 · docs · p0 · deps: T-9.1..T-9.4
- **What**: a small onboarding doc.
- **AC**: review.
- **Files**: `docs/INVITE.md`.

---

## Phase 10 — Closed beta launch

### T-10.1 [p0] [ops] Seed first invitee (operator self)
Phase 10 · ops · p0 · deps: Phase 9
- **What**: `seed_invite.py` — insert `anand@dheemantech.com`; complete sign-in; connect Claude Code; write & recall.
- **AC**: end-to-end success documented.
- **Files**: `docs/runbooks/launch_log.md`.

### T-10.2 [p0] [ops] Seed 2-3 invitees
Phase 10 · ops · p0 · deps: T-10.1
- **What**: send invites; observe issues.
- **AC**: 3/3 onboard successfully.

### T-10.3 [p1] [ops] Triage feedback for one week
Phase 10 · ops · p1 · deps: T-10.2
- **What**: address top issues.
- **AC**: feedback queue empty.

### T-10.4 [p1] [ops] Expand beta
Phase 10 · ops · p1 · deps: T-10.3
- **What**: up to ~10 invitees.
- **AC**: capacity holding; alarms green.

---

## Phase 11 — Hardening / v2 prep (out of scope for this plan)

(Listed so issues can be created with `phase-11` label as decisions arise.)

- **T-11.1** Reranker integration (Bedrock Cohere Rerank).
- **T-11.2** Agentic DCR review pipeline.
- **T-11.3** Stripe billing for tier upgrades.
- **T-11.4** Additional IdPs (GitHub, Apple, Microsoft).
- **T-11.5** Multi-region failover.
- **T-11.6** Public signup with CAPTCHA.
- **T-11.7** Async backfill ingestion.
- **T-11.8** Column-level encryption of `content`.
- **T-11.9** Tenant merge UI.
- **T-11.10** LLM-based recall preflight classifier.
- **T-11.11** MFA option.
- **T-11.12** Skill marketplace.
- **T-11.13** Bulk import (CSV/JSONL).
- **T-11.14** Webhook delivery.
- **T-11.15** API tokens (long-lived, scoped).

---

## Cross-cutting

### T-X.1 [p0] [infra] Pre-commit config
Phase 0 · infra · p0 · deps: T-3.1
- **What**: `.pre-commit-config.yaml` with ruff (lint+format), mypy, tenant-scope linter.
- **AC**: `pre-commit run --all` clean.
- **Files**: `.pre-commit-config.yaml`.

### T-X.2 [p0] [infra] CI workflows
Phase 0 · infra · p0 · deps: T-X.1
- **What**: GitHub Actions: `lint`, `unit`, `integration` (with ephemeral Postgres + Bedrock stub), `security` (REQUIRED check), `web-build` (`pnpm build`), `infra-lint` (cfn-lint).
- **AC**: all jobs lit on PR.
- **Files**: `.github/workflows/{lint,unit,integration,security,web-build,infra-lint}.yml`.

### T-X.3 [p1] [docs] ADRs (Architecture Decision Records)
Phase 1 · docs · p1 · deps: —
- **What**: `docs/adr/` — initial 5 ADRs per spec §16.1: cognito-with-dcr-shim, single-vm-postgres, titan-embed-v2-not-cris, tenant-identities-table, versioning-by-type. Plus a v1-specific one: cft-not-terraform.
- **AC**: PR review.
- **Files**: `docs/adr/0001..0006.md`.

### T-X.4 [p1] [docs] README
Phase 0 · docs · p1 · deps: —
- **What**: top-level `README.md` pointing to spec + LLD + tasks; quick-start for contributors.
- **AC**: review.
- **Files**: `README.md`.

### T-X.5 [p1] [tests] `tools/dump_schemas.py`
Phase 5 · tests · p1 · deps: T-5.2
- **What**: dumps every Pydantic model's JSON Schema into `docs/schemas/` for diffability.
- **AC**: CI step diffs current vs committed schemas; fails on uncommitted change.
- **Files**: `tools/dump_schemas.py`, `docs/schemas/.gitkeep`, `.github/workflows/schemas-diff.yml`.

---

## Dependency snapshot (rough)

```
Phase 0 (prereqs) ─┐
                   ├─► Phase 1 (CFT) ─► Phase 2 (DB) ─► Phase 3 (skeleton)
                   │                                       │
                   └─► T-X.1 / T-X.2 (CI) ──────────────────┤
                                                            ▼
                                                  Phase 4 (auth + DCR)
                                                            │
                                                            ▼
                                                  Phase 5 (MCP + tools)
                                                            │
                                                            ▼
                                                  Phase 6 (isolation gate)
                                                            │
                                                            ▼
                                          Phase 7 (lifecycle + retention) ─► Phase 8 (web)
                                                            │                       │
                                                            └────► Phase 9 (skills + ops + alarms)
                                                                                    │
                                                                                    ▼
                                                                           Phase 10 (beta launch)
```

Phase 6 is the gating phase. Phases 7 and 8 can proceed somewhat in parallel after Phase 6 passes.

---

*End of task list.*
