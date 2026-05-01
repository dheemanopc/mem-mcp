# Personal Memory MCP — Final Build Plan & Component Design (v2)

**Status:** Final, ready for implementation.
**Target executor:** Claude Code, with human review at gating phases.
**Distribution:** This document is the canonical specification. All GitHub issues should derive from §17 (Task List). Section numbers are stable; cite them in PR descriptions.

---

## Document Conventions

- **MUST / SHOULD / MAY** follow RFC 2119 semantics.
- **FR-x.y** = Functional Requirement, suitable for direct conversion into a GitHub issue.
- **NFR-x.y** = Non-Functional Requirement (performance, security, operability).
- **AC** = Acceptance Criteria for a phase or component.
- **§N.N** = section reference within this document.

---

## Table of Contents

1. Goals, scope, constraints
2. Glossary
3. High-level architecture
4. AWS resource inventory
5. Multi-tenancy & security model
6. Authentication & authorization (Cognito + DCR shim)
7. Identity linking & account lifecycle
8. Data model (full DDL)
9. MCP server: transport, tools, behaviors
10. Memory pipeline: write, search, dedupe, version
11. Quotas, tiers, abuse controls
12. Web application (admin UI)
13. Skills & client integration
14. Operations: deployment, backup, retention, observability
15. Threat model
16. Repository layout & coding standards
17. Phase plan & full task list (GitHub-issue-ready)
18. Testing strategy
19. Open questions / future work
20. Appendices

---

## 1. Goals, Scope, Constraints

### 1.1 Product summary

A multi-tenant, DPDP-aligned, AWS Mumbai–hosted memory service. AI clients (Claude Code, Claude.ai, ChatGPT, others) connect over MCP with OAuth 2.1 + Dynamic Client Registration. Each user has a private memory store accessed by their AI assistants for capture (write) and recall (search) of decisions, facts, snippets, and notes across sessions and across clients.

### 1.2 In scope (v1)

- Memory storage with hybrid retrieval (semantic + keyword + recency).
- OAuth 2.1 with DCR via Cognito + shim.
- Multi-IdP per tenant (Google, GitHub) with linking and account-level operations.
- Admin web UI for memory management, identity management, stats, DPDP operations.
- Closed beta: invitation-gated signup; Premium tier default; quotas enforced.
- DCR allowlist of known AI clients with future-ready review states.
- Audit log of all auth and data events.
- Region pinned to `ap-south-1`.

### 1.3 Out of scope (v1, deferred to v2)

- Billing / Stripe integration.
- Agentic DCR verification (replaces static allowlist).
- Reranker in retrieval pipeline.
- LLM-based recall preflight classifier.
- Cross-tenant sharing / teams.
- Multi-region failover.
- Tenant merging UI (operator-only manual SQL in v1).
- Public open signup.
- Async backfill from Claude Code transcripts.
- Column-level encryption of `content`.
- Apple sign-in.

### 1.4 Non-functional priorities (ranked)

1. **Tenant isolation.** No cross-user leakage under any failure mode.
2. **Retrieval quality.** Results that feel relevant.
3. **Low cost.** Flat operating cost during closed beta (~$30–40/month).
4. **Time to v1.** Secondary to the above three.

### 1.5 Constraints

- **Region:** `ap-south-1` (Mumbai). All durable data MUST stay in-region.
- **Identity & secrets:** no plaintext secrets on disk; SSM Parameter Store only.
- **Network:** single VM with public IP; no NAT gateway; security group restricts ingress.
- **Compliance:** DPDP Act (India) — residency, deletion-on-request, audit retention 90 days post-anonymization, consent on signup.
- **Clients:** Claude Code, Claude.ai (Pro/Max custom connector), ChatGPT (developer mode connector).
- **Stack lock-ins:** Python 3.12, FastAPI, asyncpg, PostgreSQL 16, pgvector, Cognito, Bedrock Titan Embed v2, Caddy, systemd, Terraform.

---

## 2. Glossary

| Term | Definition |
|---|---|
| Tenant | A single human user's account; primary entity for data ownership. |
| Identity | A federated sign-in linked to a tenant (one Google account = one identity). |
| Memory | A single stored unit: content + embedding + tags + type + timestamps. |
| Client | An OAuth-registered MCP client (Claude Code instance, Claude.ai connector, etc.). |
| Cognito user | A user record in the Cognito User Pool, bound to one IdP identity. |
| Cognito sub | Stable opaque ID Cognito issues per Cognito user. |
| Software ID | The DCR `software_id` claim identifying the AI client product. |
| Tier | Quota class: Standard, Premium, Gold, Platinum. |
| RLS | PostgreSQL Row Level Security. |
| DCR | Dynamic Client Registration (RFC 7591). |
| PRM | Protected Resource Metadata (RFC 9728). |
| MCP | Model Context Protocol. |
| FR / NFR / AC | Functional / Non-Functional Requirement / Acceptance Criteria. |

---

## 3. High-Level Architecture

### 3.1 Component diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│  AI clients                                                             │
│  ┌────────────┐   ┌────────────┐   ┌────────────┐   ┌──────────────┐    │
│  │ Claude Code│   │ Claude.ai  │   │ ChatGPT    │   │ Other MCP    │    │
│  │            │   │ chat       │   │ MCP        │   │ clients      │    │
│  └─────┬──────┘   └─────┬──────┘   └─────┬──────┘   └──────┬───────┘    │
└────────┼─────────────────┼────────────────┼─────────────────┼──────────-┘
         │  HTTPS / OAuth 2.1 + PKCE / Streamable HTTP MCP    │
         └────────────────┬┴────────────────────────────────-┬┘
                          ▼                                  ▼
               ┌─────────────────────┐         ┌─────────────────────┐
               │  Caddy (TLS, :443)  │         │  Cognito Hosted UI  │
               │  mem.<domain>       │         │  auth.<domain>      │
               │  app.<domain>       │         │  (user pool sign-in)│
               └──────────┬──────────┘         └──────────┬──────────┘
                          │                               │
            ┌─────────────┴────────────┐                  │
            ▼                          ▼                  │
    ┌───────────────────┐    ┌───────────────────┐        │
    │  mem-mcp app      │    │  mem-web app      │        │
    │  (FastAPI :8080)  │    │  (Next.js :8081)  │        │
    │  • OAuth shim     │    │  • Admin UI       │        │
    │  • MCP server     │    │  • Memory mgmt    │        │
    │  • Tool handlers  │    │  • Identity mgmt  │        │
    │  • Bedrock client │    │  • Stats / DPDP   │        │
    └─────────┬─────────┘    └─────────┬─────────┘        │
              │                        │                  │
              ▼                        ▼                  │
    ┌─────────────────────────────────────────┐           │
    │  PostgreSQL 16 + pgvector (localhost)   │           │
    │  RLS enforced; mem_app + mem_maint roles│           │
    └─────────────────────────────────────────┘           │
              │                                           │
              ▼                                           │
    ┌─────────────────────────────────────────┐           │
    │  AWS services (ap-south-1):             │           │
    │  • Cognito User Pool ◀──────────────────┼───────────┘
    │  • Bedrock Titan Embed v2               │
    │  • SSM Parameter Store                  │
    │  • Lambda (Cognito PreSignUp trigger)   │
    │  • S3 (encrypted backups)               │
    │  • CloudWatch (logs, alarms)            │
    │  • SES (transactional email)            │
    │  • KMS (CMK)                            │
    │  • Route 53 (DNS)                       │
    └─────────────────────────────────────────┘

  Single EC2 VM: t4g.medium, Ubuntu 24.04 ARM64
  Public IP via Elastic IP; SG: 443/80 from 0.0.0.0/0, 22 from operator IP only.
```

### 3.2 Why this shape (recap of decisions made in design discussion)

- **Single VM with co-located Postgres**: lowest cost, lowest latency, simplest backups; correct for ~10s of users in beta.
- **Cognito + DCR shim**: no hand-rolled OAuth (DCR shim is ~250 LOC, Cognito owns the rest); preserves DPDP residency since Cognito is `ap-south-1` native.
- **Bedrock Titan Embed v2**: native to `ap-south-1`, no cross-region inference.
- **No Bedrock LLM in v1 hot path**: Claude/Nova in Mumbai are CRIS-only (data may leave region); use heuristic gates client-side via skills.
- **Two app processes** (`mem-mcp` and `mem-web`): clean separation of concerns; admin UI changes don't redeploy MCP, and vice versa. Same VM, same DB.

### 3.3 Logical data flow

**Memory write (typical):**

```
AI client → POST /mcp (Bearer JWT)
  → Caddy → mem-mcp
  → middleware: validate JWT against Cognito JWKS
  → middleware: lookup tenant_identities by cognito_sub → tenant_id
  → tenant_tx(tenant_id) opens transaction with SET LOCAL app.current_tenant_id
  → handler memory.write:
    - validate input
    - check quotas (memories count, daily token budget)
    - compute content hash
    - call Bedrock Titan v2 → embedding (1024 dim)
    - dedupe check: hash match OR cosine > 0.95 in last 50 memories of tenant
    - on dup: update existing memory (merge tags, bump updated_at)
    - on new: insert memory row
    - increment tenant_daily_usage
    - emit audit_log row
  → commit transaction
  → return {id, deduped: bool, version: 1}
```

**Memory search (typical):**

```
AI client → POST /mcp (Bearer JWT)
  → Caddy → mem-mcp
  → middleware: validate JWT, resolve tenant_id
  → tenant_tx
  → handler memory.search:
    - validate input
    - check rate limits
    - call Bedrock Titan v2 → query embedding
    - run hybrid query (§10.3): semantic top 50 ∪ keyword top 50 → score with recency_lambda by type → top k
    - emit audit_log row
  → commit
  → return [{id, content, type, tags, created_at, score}, ...]
```

**OAuth flow (first-time client):**

```
AI client → POST /mcp (no token)
  → mem-mcp returns 401 + WWW-Authenticate w/ resource metadata pointer
AI client → GET /.well-known/oauth-protected-resource → mem-mcp
  → returns { authorization_servers: ["https://mem.<domain>"] }
AI client → GET /.well-known/oauth-authorization-server → mem-mcp
  → returns metadata pointing at Cognito endpoints AND our /oauth/register
AI client → POST /oauth/register (DCR payload)
  → mem-mcp validates allowlist (software_id), redirect URIs
  → mem-mcp calls Cognito CreateUserPoolClient
  → mem-mcp returns RFC 7591 response with new client_id
AI client → opens browser → Cognito Hosted UI authorize URL with PKCE
  → user signs in (Google or GitHub federation)
  → Cognito issues code → redirected back to AI client
AI client → POST Cognito /oauth2/token with code+verifier
  → Cognito returns access_token (JWT), refresh_token, id_token
AI client → POST /mcp with Bearer access_token
  → mem-mcp validates → authenticated → resolves tenant → serves request
```

---

## 4. AWS Resource Inventory

All resources MUST be Terraform-managed unless explicitly noted as console-only.

### 4.1 Networking

- **VPC** `mem-mcp-vpc`: CIDR `10.0.0.0/16`.
- **Public subnet** `mem-mcp-subnet-public-a`: `10.0.1.0/24`, AZ `ap-south-1a`.
- **Internet Gateway** attached.
- **Route table** `mem-mcp-rt-public`: default route → IGW.
- **Security group** `mem-mcp-sg`:
  - Ingress 443/tcp from `0.0.0.0/0`
  - Ingress 80/tcp from `0.0.0.0/0` (ACME challenge only; Caddy serves)
  - Ingress 22/tcp from operator IP (param: `ssm:/mem-mcp/ops/operator_cidr`)
  - Egress 443/tcp to `0.0.0.0/0`
  - Egress 587/tcp to AWS SES endpoints (only if SES SMTP is used; we'll use SES API instead)

### 4.2 Compute & storage

- **EC2 instance** `mem-mcp-host`: `t4g.medium` (2 vCPU, 4 GB), Ubuntu 24.04 ARM64.
- **EBS root** `mem-mcp-root`: gp3, 30 GB, encrypted with KMS `alias/mem-mcp`.
- **Elastic IP** `mem-mcp-eip`: associated with EC2.
- **EBS snapshot policy** `mem-mcp-snap-daily`: daily, 7-day retention, encrypted.

### 4.3 Backup

- **S3 bucket** `mem-mcp-backups-${account}-aps1`:
  - Versioning ON
  - Encryption: SSE-KMS with `alias/mem-mcp`
  - Block Public Access: ALL ON
  - Bucket policy: deny non-TLS, deny non-KMS uploads, restrict to IAM role `mem-mcp-instance-role`
  - Lifecycle: Standard 30d → Glacier IR 365d → expire 730d

### 4.4 Identity (Cognito)

- **Cognito User Pool** `mem-mcp-pool`:
  - Username attribute: `email`
  - Required attributes: `email` (verified)
  - MFA: optional in v1 (settable per user)
  - Password policy: irrelevant (federation only); default settings fine
  - Lambda triggers: PreSignUp → `mem-mcp-presignup`
  - Account recovery: email
  - Token expiration: access 60min, id 60min, refresh 30 days
  - Advanced security mode: Audit (free tier — ENFORCED tier costs)
- **Cognito User Pool Domain** `auth.<your-domain>` (custom domain, ACM cert in `us-east-1` for CloudFront-fronted Cognito... actually Cognito custom domains require ACM cert in `us-east-1`; this is the one cross-region resource we accept).
- **Identity Providers**:
  - Google (configured with Google OAuth client credentials from Google Console; secret in SSM)
  - GitHub (via OIDC connector; secret in SSM)
- **App clients (initial)**:
  - `mem-web-client`: confidential, used by the Next.js web app, supports Authorization Code with PKCE
  - DCR-created clients for AI tools created on demand by the shim
- **Resource server** `mem-mcp-api`:
  - Identifier: `https://mem.<your-domain>`
  - Custom scopes: `memory.read`, `memory.write`, `memory.admin`, `account.manage`

### 4.5 Lambda

- **`mem-mcp-presignup`**: Cognito PreSignUp trigger. Reads `invited_emails` allowlist (via Postgres via Lambda's RDS-Data... actually we'll do this via direct connection from a Lambda inside the same VPC, OR simpler: the Lambda hits an internal endpoint on mem-mcp `/internal/check_invite` protected by a shared secret in SSM). Allows or rejects signup.

### 4.6 Bedrock

- **Model access enabled**: `amazon.titan-embed-text-v2:0` in `ap-south-1` (must be enabled via console; cannot be Terraform).
- **No Claude/Nova access requested** in v1 to keep clear DPDP posture.

### 4.7 Email (SES)

- **Verified domain identity**: `<your-domain>` in `ap-south-1`.
- **DKIM** enabled.
- **Configuration set** `mem-mcp-ses`: tracks bounces/complaints to CloudWatch.
- **Sandbox removal**: must be requested before launch (multi-day support ticket).
- **From addresses**: `noreply@<your-domain>`, `support@<your-domain>`.

### 4.8 Secrets & config

- **KMS key** `mem-mcp-key` (alias `alias/mem-mcp`): customer-managed, rotation enabled.
- **SSM Parameter Store** (`SecureString` unless noted):
  - `/mem-mcp/db/password` (mem_app DB password)
  - `/mem-mcp/db/maint_password` (mem_maint DB password)
  - `/mem-mcp/cognito/user_pool_id` (String)
  - `/mem-mcp/cognito/region` (String)
  - `/mem-mcp/cognito/web_client_id` (String)
  - `/mem-mcp/cognito/web_client_secret`
  - `/mem-mcp/cognito/google_client_id`
  - `/mem-mcp/cognito/google_client_secret`
  - `/mem-mcp/cognito/github_client_id`
  - `/mem-mcp/cognito/github_client_secret`
  - `/mem-mcp/internal/lambda_shared_secret` (PreSignUp ↔ /internal/check_invite)
  - `/mem-mcp/ses/from_email` (String)
  - `/mem-mcp/backup/gpg_passphrase`
  - `/mem-mcp/ops/operator_cidr` (String)
  - `/mem-mcp/web/session_secret`

### 4.9 IAM

- **Instance role** `mem-mcp-instance-role`. Policy permissions:
  - `bedrock:InvokeModel` on resource ARN of `amazon.titan-embed-text-v2:0` in `ap-south-1`
  - `cognito-idp:CreateUserPoolClient`, `cognito-idp:DeleteUserPoolClient`, `cognito-idp:UpdateUserPoolClient`, `cognito-idp:DescribeUserPoolClient`, `cognito-idp:ListUserPoolClients` on the user pool
  - `cognito-idp:AdminGetUser`, `cognito-idp:AdminLinkProviderForUser`, `cognito-idp:AdminDisableUser`, `cognito-idp:AdminDeleteUser` on the user pool
  - `ses:SendEmail`, `ses:SendRawEmail` from `noreply@<your-domain>` and `support@<your-domain>`
  - `s3:PutObject`, `s3:GetObject`, `s3:ListBucket` on backup bucket
  - `ssm:GetParameter`, `ssm:GetParameters`, `ssm:GetParametersByPath` on `/mem-mcp/*`
  - `kms:Decrypt`, `kms:Encrypt`, `kms:GenerateDataKey` on `alias/mem-mcp`
  - `logs:CreateLogStream`, `logs:PutLogEvents` on its log group
- **Lambda execution role** `mem-mcp-lambda-presignup-role`: invoke /internal endpoint via VPC, write to CloudWatch.

### 4.10 DNS & TLS

- **Route 53 hosted zone**: `<your-domain>`
- **Records**:
  - `mem.<domain>` A → Elastic IP (MCP server)
  - `app.<domain>` A → Elastic IP (web UI)
  - `auth.<domain>` ALIAS → Cognito custom domain
- **TLS**: Caddy auto-provisions for `mem` and `app` via Let's Encrypt; ACM cert in `us-east-1` for Cognito's `auth` subdomain.

### 4.11 Observability

- **CloudWatch log groups**:
  - `/mem-mcp/app` (30-day retention)
  - `/mem-mcp/web` (30-day retention)
  - `/mem-mcp/lambda/presignup` (90-day retention)
- **CloudWatch alarms** (see §14.4 for full list).
- **CloudWatch dashboard** `mem-mcp-overview`.

### 4.12 Cost estimate (10 active beta users)

| Item | Monthly est. |
|---|---|
| EC2 t4g.medium on-demand | $24 |
| EBS 30 GB gp3 | $3 |
| Daily snapshots (7d) | $1 |
| S3 backup storage | $1 |
| Cognito (free tier covers <50k MAUs) | $0 |
| Bedrock Titan embeddings (~100k tokens/day) | $0.06 |
| SES (1k emails) | $0.10 |
| Route 53 hosted zone | $0.50 |
| KMS (1 CMK + ops) | $1 |
| CloudWatch logs/alarms | $2 |
| Lambda invocations | $0 |
| Data transfer out | $1 |
| ACM (us-east-1, free) | $0 |
| **Total** | **~$33/month** |

Reserved instance: t4g.medium 1y savings plan ≈ $15/mo, cuts total to ~$25/mo.

---
## 5. Multi-Tenancy & Security Model

This section is **non-negotiable**. Every other component conforms to the rules here.

### 5.1 Tenant identity (FRs)

- **FR-5.1.1** A tenant is the primary entity for data ownership. `tenants.id` is a UUIDv4 generated by Postgres.
- **FR-5.1.2** A tenant has 1..N identities, stored in `tenant_identities`. Each identity is a Cognito user (one IdP sign-in).
- **FR-5.1.3** Every authenticated request resolves to exactly one `tenant_id` via `tenant_identities.cognito_sub`.
- **FR-5.1.4** Memories and audit rows are owned by `tenant_id`, never by `cognito_sub`.
- **FR-5.1.5** Email is derived from the IdP claim. Multiple identities may have the same email. Email is not unique on `tenant_identities`.
- **FR-5.1.6** A tenant has exactly one `is_primary` identity at any time.

### 5.2 Tenant isolation enforcement (NFRs)

Three independent layers, all required:

#### Layer 1: Application-level

- **NFR-5.2.1** Every MCP request MUST present a Bearer JWT with valid signature against Cognito's JWKS, valid `exp`, valid `iss` (Cognito issuer URL), and valid `aud` (the Cognito app client ID OR the resource server identifier — see §6.4).
- **NFR-5.2.2** The middleware MUST extract `sub`, look up `tenant_identities` for that `cognito_sub`, and reject with 401 if absent.
- **NFR-5.2.3** Tenant `status` MUST be checked. `suspended` → 403 with `account_suspended`. `pending_deletion` → 403 with `account_deletion_pending`. `deleted` → 401 with no further info.
- **NFR-5.2.4** Failures MUST audit-log with `result='denied'` and reason. Successful auths log `result='success'` at debug level.

#### Layer 2: PostgreSQL Row Level Security

- **NFR-5.2.5** The `memories` table MUST have RLS ENABLED and FORCED. The policy filters by `tenant_id = current_setting('app.current_tenant_id', true)::uuid`.
- **NFR-5.2.6** When `app.current_tenant_id` is missing, RLS MUST return zero rows (fail-closed). Verified by automated test (§18).
- **NFR-5.2.7** Same RLS treatment applies to: `tenant_daily_usage`, any other table containing per-tenant data.

#### Layer 3: Explicit predicate

- **NFR-5.2.8** Every query that touches per-tenant data MUST include explicit `WHERE tenant_id = $1` AND rely on RLS as backstop. RLS is the safety net; explicit predicate is the contract.
- **NFR-5.2.9** A linter (custom pytest plugin) MUST scan `src/mem_mcp/` for SQL strings touching `memories` without `tenant_id` and fail CI.

### 5.3 Connection pool tenancy rule

- **NFR-5.3.1** The application MUST use a `tenant_tx(pool, tenant_id)` async context manager that:
  1. Acquires a connection from the pool.
  2. Opens a transaction.
  3. Executes `SELECT set_config('app.current_tenant_id', $1, true)` (the `true` makes it `LOCAL`).
  4. Yields the connection.
  5. Commits/rolls back; releases connection.
- **NFR-5.3.2** No code path may call `SET app.current_tenant_id` (without LOCAL).
- **NFR-5.3.3** A pytest fixture asserts that pool reuse across concurrent tenants does not leak the setting (§18.3).

### 5.4 Encryption

- **NFR-5.4.1 (in transit)** TLS 1.3 at Caddy. HSTS `max-age=31536000; includeSubDomains; preload`. Internal localhost calls (app → Postgres) over Unix socket; not encrypted but bounded.
- **NFR-5.4.2 (at rest, EBS)** EBS encrypted with KMS `alias/mem-mcp`.
- **NFR-5.4.3 (at rest, Postgres)** EBS-level encryption sufficient for v1. Column-level encryption deferred (§19).
- **NFR-5.4.4 (at rest, S3)** SSE-KMS with `alias/mem-mcp`. GPG passphrase encryption applied **before** upload by `pg_dump_to_s3.sh` for defense in depth.
- **NFR-5.4.5 (secrets)** All secrets in SSM SecureString. The application reads them at startup via the EC2 instance role.

### 5.5 Audit log

- **FR-5.5.1** Every mutation, every search, every auth event writes a row to `audit_log`.
- **FR-5.5.2** Schema (full DDL in §8): `tenant_id, actor_client_id, action, target_id, ip_address, user_agent, request_id, result, details, created_at`.
- **FR-5.5.3** `audit_log` is append-only at the application layer. The `mem_app` role has INSERT only. UPDATE/DELETE is granted only to `mem_maint`.
- **FR-5.5.4** Audit rows are retained for 730 days, then purged by maintenance job. After tenant deletion, audit rows referencing that tenant are anonymized at the 90-day mark (replace `tenant_id` with NULL, keep `details` minus any PII).
- **FR-5.5.5** Actions MUST include at minimum: `auth.token_issued`, `auth.token_refresh`, `auth.token_refresh_reuse`, `auth.token_revoked`, `auth.session_started`, `oauth.dcr_register`, `oauth.dcr_rejected`, `oauth.client_revoked`, `tenant.created`, `tenant.suspended`, `tenant.deletion_requested`, `tenant.deletion_cancelled`, `tenant.deleted`, `identity.linked`, `identity.unlinked`, `memory.write`, `memory.search`, `memory.get`, `memory.list`, `memory.update`, `memory.delete`, `memory.undelete`, `memory.supersede`, `memory.export`, `memory.feedback`, `memory.dedupe_merged`, `quota.exceeded`, `ratelimit.exceeded`.

### 5.6 Retention

- **FR-5.6.1** Per-tenant `retention_days`, default 365, configurable per-tenant via the web UI.
- **FR-5.6.2** Memories with `created_at < now() - retention_days` are soft-deleted (`deleted_at = now()`) by nightly job.
- **FR-5.6.3** Soft-deleted memories are recoverable via web UI for 30 days.
- **FR-5.6.4** Memories with `deleted_at < now() - 30 days` are hard-deleted.
- **FR-5.6.5** DPDP deletion requests override: hard-delete within 7 days of confirmation.

### 5.7 Network & host hardening

- **NFR-5.7.1** No NAT gateway. EC2 in public subnet with public IP.
- **NFR-5.7.2** Security group locked per §4.1.
- **NFR-5.7.3** SSH: key-only, no root login, fail2ban, port 22 restricted by SG.
- **NFR-5.7.4** `unattended-upgrades` enabled for security patches.
- **NFR-5.7.5** Postgres listens on `127.0.0.1` only. App uses Unix socket where supported by asyncpg, else loopback.
- **NFR-5.7.6** Caddy adds: `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Content-Security-Policy` (web app only, strict).
- **NFR-5.7.7** CORS: `mem-mcp` MUST validate `Origin` for browser-driven requests; explicit allowlist (Cognito Hosted UI domain, app subdomain) — never `*` with credentials.

---

## 6. Authentication & Authorization

### 6.1 Architecture

Cognito is the authorization server. The mem-mcp app provides a thin DCR shim plus PRM metadata. AI clients discover and register through the shim; user authentication and token issuance are pure Cognito.

### 6.2 Endpoints exposed by mem-mcp

| Path | Method | Purpose | Auth |
|---|---|---|---|
| `/.well-known/oauth-protected-resource` | GET | RFC 9728 resource metadata | none |
| `/.well-known/oauth-authorization-server` | GET | Proxied/synthesized AS metadata; advertises DCR | none |
| `/oauth/register` | POST | RFC 7591 DCR shim | none + rate limit |
| `/oauth/register/{client_id}` | GET | Read registration | registration_access_token |
| `/oauth/register/{client_id}` | DELETE | Delete client | registration_access_token |
| `/internal/check_invite` | POST | PreSignUp Lambda → invite allowlist check | shared secret header |
| `/mcp` | POST | MCP transport | Bearer JWT |
| `/healthz` | GET | Liveness | none |
| `/readyz` | GET | Readiness (checks DB + Bedrock + Cognito) | none |

### 6.3 PRM document (`/.well-known/oauth-protected-resource`)

```json
{
  "resource": "https://mem.<your-domain>",
  "authorization_servers": ["https://mem.<your-domain>"],
  "scopes_supported": ["memory.read", "memory.write", "memory.admin"],
  "bearer_methods_supported": ["header"],
  "resource_documentation": "https://mem.<your-domain>/docs"
}
```

### 6.4 AS metadata (`/.well-known/oauth-authorization-server`)

Synthesized by the shim, pointing at Cognito for actual flow endpoints but advertising our `registration_endpoint`:

```json
{
  "issuer": "https://mem.<your-domain>",
  "authorization_endpoint": "https://auth.<your-domain>/oauth2/authorize",
  "token_endpoint":         "https://auth.<your-domain>/oauth2/token",
  "jwks_uri":               "https://cognito-idp.<region>.amazonaws.com/<userpool_id>/.well-known/jwks.json",
  "registration_endpoint":  "https://mem.<your-domain>/oauth/register",
  "scopes_supported":       ["openid","email","profile","memory.read","memory.write","memory.admin"],
  "response_types_supported": ["code"],
  "grant_types_supported":  ["authorization_code","refresh_token"],
  "token_endpoint_auth_methods_supported": ["none","client_secret_post"],
  "code_challenge_methods_supported": ["S256"],
  "service_documentation":  "https://mem.<your-domain>/docs"
}
```

### 6.5 DCR shim behavior

#### Inputs (RFC 7591)

```json
{
  "client_name": "Claude Code",
  "client_uri": "https://claude.com/code",
  "redirect_uris": ["http://localhost:8080/callback"],
  "grant_types": ["authorization_code","refresh_token"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none",
  "scope": "memory.read memory.write",
  "software_id": "claude-code",
  "software_version": "2.x"
}
```

#### Validation rules (FRs)

- **FR-6.5.1** Reject requests over 8 KB.
- **FR-6.5.2** `redirect_uris`: each MUST be HTTPS, OR `http://localhost[:port][/path]`, OR `http://127.0.0.1[:port][/path]`. No wildcards. No fragments. Limit 5.
- **FR-6.5.3** `grant_types` MUST be subset of `[authorization_code, refresh_token]`.
- **FR-6.5.4** `token_endpoint_auth_method` MUST be `none` (public clients).
- **FR-6.5.5** `code_challenge_methods` defaults to `S256`; `plain` rejected.
- **FR-6.5.6** `scope` validated against allowed scopes; default `memory.read memory.write`.
- **FR-6.5.7** **Software allowlist check**: `software_id` MUST exist in `allowed_software` with `status='allowed'`. If unknown OR `status='blocked'`, return `403` with structured error `{"error":"unauthorized_client", "error_description":"Client not in allowlist"}` AND audit row.
- **FR-6.5.8** `client_name` length ≤ 128, must match Cognito's allowed character set `[\w\s+=,.@-]+`. Sanitize by replacing non-matching chars with `-`.
- **FR-6.5.9** Per-IP rate limit: 5 registrations / hour. Global: 100 registrations / day across all IPs.

#### Outputs (RFC 7591)

```json
{
  "client_id": "<cognito-generated>",
  "client_id_issued_at": 1714400000,
  "client_secret_expires_at": 0,
  "redirect_uris": ["..."],
  "grant_types": ["authorization_code","refresh_token"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none",
  "scope": "memory.read memory.write",
  "registration_access_token": "<one-time random, hashed in DB>",
  "registration_client_uri": "https://mem.<your-domain>/oauth/register/<client_id>"
}
```

#### Internal flow

1. Validate per FR-6.5.x.
2. Begin transaction.
3. Audit log `oauth.dcr_register` with full payload (sanitized) + IP.
4. Call Cognito `CreateUserPoolClient` with: `ClientName=<sanitized>`, `CallbackURLs=<redirect_uris>`, `AllowedOAuthFlows=["code"]`, `AllowedOAuthFlowsUserPoolClient=true`, `AllowedOAuthScopes=<scopes>`, `SupportedIdentityProviders=["COGNITO","Google","GitHub"]`, `GenerateSecret=false`, `EnableTokenRevocation=true`.
5. Capture returned Cognito `ClientId`.
6. INSERT into `oauth_clients` (id=ClientId, registration_payload=raw, review_status='auto_allowed', created_at=now()).
7. Generate `registration_access_token` (32 bytes urlsafe base64), store SHA-256 hash.
8. Commit; return RFC 7591 response.

#### Cleanup job

- **FR-6.5.10** Daily cron deletes Cognito clients where: never used (`oauth_clients.last_used_at IS NULL` and `created_at < now() - 24 hours`) OR unused for 90 days. Logs each deletion.

### 6.6 Cognito configuration (FRs)

- **FR-6.6.1** User pool created in `ap-south-1` with email as username attribute.
- **FR-6.6.2** PreSignUp Lambda trigger configured to `mem-mcp-presignup`.
- **FR-6.6.3** Federated IdPs: Google, GitHub.
- **FR-6.6.4** Hosted UI customized with logo, color, link to Privacy Policy + ToS (legal pages on `app.<your-domain>`).
- **FR-6.6.5** Custom scopes configured on the resource server `mem-mcp-api`: `memory.read`, `memory.write`, `memory.admin`, `account.manage`.
- **FR-6.6.6** Token customization (Pre Token Generation Lambda) — **deferred**; v1 uses default JWT shape. The `sub` claim is the natural per-Cognito-user identifier.
- **FR-6.6.7** Refresh token reuse detection: Cognito's built-in token revocation enabled.
- **FR-6.6.8** Advanced Security Mode: AUDIT (free) for v1; consider ENFORCED for v2.

### 6.7 Custom consent screen

Cognito's hosted UI does not show client identity at consent in a customizable way useful for our trust model. We interpose our own consent screen:

- **FR-6.7.1** A custom OAuth proxy on the mem-mcp app intercepts the authorize URL: `/oauth/authorize?client_id=...&...` redirects to a consent page that displays:
  - Client name (with verified badge if `software_id` is in allowlist with `verified=true`)
  - Vendor (e.g., "Anthropic", "OpenAI")
  - Requested scopes in plain English
  - Warning banner if unverified
  - A button "Authorize" → continues to Cognito Hosted UI
  - A button "Cancel" → returns error to client with `error=access_denied`
- **FR-6.7.2** The consent screen is shown per (tenant × client) combination, recorded in `oauth_consents` table, and skipped on subsequent flows for the same combination unless the user revokes.
- **FR-6.7.3** Consent grant is bound to client_id + scopes; if scopes expand, re-consent required.

### 6.8 JWT validation

- **FR-6.8.1** Validate JWT with Cognito JWKS (cached 1h, refreshed on `kid` miss).
- **FR-6.8.2** Check `iss == https://cognito-idp.<region>.amazonaws.com/<userpool_id>`.
- **FR-6.8.3** Check `aud` matches the Cognito client_id of the requesting app, OR check the `client_id` claim is in `oauth_clients`.
- **FR-6.8.4** Check `exp > now()`, `nbf <= now()` (if present), `iat <= now() + 60s`.
- **FR-6.8.5** Extract `sub`, find tenant via `tenant_identities`, set request context.
- **FR-6.8.6** Extract `scope` claim; downstream tools enforce required scopes.

---

## 7. Identity Linking & Account Lifecycle

### 7.1 Concepts

- A tenant has one or more identities. Each identity is a Cognito user.
- Signing in with any linked identity resolves to the same tenant.
- Auto-creation of new tenant happens only if no existing identity matches AND user explicitly chose "create new account" at signup.

### 7.2 Sign-in flow (FRs)

- **FR-7.2.1** A Cognito sign-in returns a JWT with `sub`. Backend looks up `tenant_identities` by `cognito_sub`.
- **FR-7.2.2** If found and tenant active → success.
- **FR-7.2.3** If not found → branch:
  - **For first-time invitation:** PreSignUp Lambda already validated `invited_emails`. Backend creates `tenants` row + first `tenant_identities` row (primary=true).
  - **For email collision:** Lambda detects existing tenant with same email. Returns Lambda denial with code `EMAIL_COLLISION_TENANT_EXISTS`. Cognito surfaces this to the user via Hosted UI as a friendly error directing them to the link flow.
- **FR-7.2.4** Tenant statuses are honored: `suspended` → blocked, `pending_deletion` → blocked, `deleted` → not found.

### 7.3 Email collision handling (FR-7.3.x)

- **FR-7.3.1** Cognito PreSignUp Lambda receives signup attempt with email + IdP attributes.
- **FR-7.3.2** Lambda calls `/internal/check_invite` on mem-mcp with email and IdP info.
- **FR-7.3.3** mem-mcp `/internal/check_invite` returns one of:
  - `{"decision": "allow", "reason": "invited"}` — fresh invitation, no existing tenant.
  - `{"decision": "deny", "reason": "not_invited"}` — email not on allowlist.
  - `{"decision": "deny", "reason": "email_belongs_to_existing_tenant", "existing_tenant_hint": "<email_masked>"}` — email is already tied to a tenant via another identity. UI must direct user to "sign in with the original method, then add this one in Settings."
- **FR-7.3.4** Lambda translates response to Cognito PreSignUp result.
- **FR-7.3.5** UI displays appropriate friendly message.

### 7.4 Linking flow (FR-7.4.x)

- **FR-7.4.1** Authenticated user (logged into web UI as tenant `T1`) initiates link from Settings.
- **FR-7.4.2** Web app generates a signed `link_state` containing: `tenant_id=T1`, `nonce`, `expires_at=now()+10min`, HMAC over the rest with key from SSM. Stored in cookie + sent as `state` parameter.
- **FR-7.4.3** Web app starts a fresh OAuth flow, but with a marker `link_mode=true` carried in `state`.
- **FR-7.4.4** User authenticates with the second IdP. PreSignUp Lambda, upon seeing `link_mode=true` in user attributes (passed via custom field), allows the new Cognito user creation but does NOT create a tenant.
- **FR-7.4.5** Callback handler in web app receives the new tokens, extracts new `cognito_sub`, validates `link_state` HMAC, ensures user's web session is still authenticated as `T1`.
- **FR-7.4.6** Insert `tenant_identities` row: `(cognito_sub=<new>, tenant_id=T1, provider=<idp>, is_primary=false)`.
- **FR-7.4.7** Audit log: `identity.linked`.
- **FR-7.4.8** Show success in UI.

#### Linking constraints

- **FR-7.4.9** A single `cognito_sub` MUST NOT be linked to multiple tenants. Enforced by UNIQUE constraint.
- **FR-7.4.10** If a `cognito_sub` is already linked, the link operation returns 409 with `identity_already_linked`.
- **FR-7.4.11** Maximum 5 identities per tenant in v1 (configurable, soft limit). Web UI enforces; backend enforces too.

### 7.5 Unlinking (FR-7.5.x)

- **FR-7.5.1** From Settings, user can unlink a non-primary identity.
- **FR-7.5.2** Unlinking the primary identity is allowed only if another identity exists; the user must promote one to primary first.
- **FR-7.5.3** Unlinking the last remaining identity is **refused** with 409 `cannot_unlink_last_identity`. The UI explains: add another, or use Account Closure if leaving.
- **FR-7.5.4** On unlink: revoke tokens for the corresponding Cognito client sessions belonging to that identity (best-effort), delete `tenant_identities` row, delete the Cognito user via `AdminDeleteUser`. Audit log `identity.unlinked`.

### 7.6 Promote primary

- **FR-7.6.1** User selects a non-primary identity and promotes it. Backend uses a transaction: clear existing primary, set new primary.

### 7.7 Account closure (FR-7.7.x)

- **FR-7.7.1** From Settings, "Close account" button leads to a confirmation page listing the consequences.
- **FR-7.7.2** User must re-authenticate (Cognito step-up) to confirm.
- **FR-7.7.3** Backend marks `tenants.status = 'pending_deletion'`, sets `deletion_requested_at = now()`, generates `deletion_cancel_token`, hashes it, stores hash.
- **FR-7.7.4** Immediately revoke all OAuth tokens: call Cognito `RevokeToken` for each refresh token if available, otherwise mark all `oauth_clients` for this tenant as `disabled`. After this, all MCP requests fail 403 `account_deletion_pending`.
- **FR-7.7.5** Confirmation email sent via SES with cancel link valid for 24 hours.
- **FR-7.7.6** Web UI shows banner during the 24h window: "Account scheduled for deletion. Cancel" — clicking reverts status to `active` and clears `deletion_*` columns. Audit `tenant.deletion_cancelled`.
- **FR-7.7.7** Nightly job processes `pending_deletion` tenants whose `deletion_requested_at < now() - 24h`:
  1. Hard-delete all memories and `tenant_daily_usage`.
  2. Delete all `tenant_identities` rows; for each, call Cognito `AdminDeleteUser`.
  3. Delete all `oauth_clients` belonging to this tenant; for each, call Cognito `DeleteUserPoolClient`.
  4. Anonymize `tenants` row: `email -> deleted-<id>@invalid`, `display_name -> NULL`, `status -> 'deleted'`.
  5. Audit `tenant.deleted`.
- **FR-7.7.8** Audit rows referencing this tenant remain for 90 days, then their `tenant_id` is nulled and any `details` PII redacted by another job.

### 7.8 Account recovery (operator runbook only in v1)

- **FR-7.8.1** If a user loses access to all identities, recovery is manual.
- **FR-7.8.2** Operator runbook: verify identity via email reply chain to the email on `tenants` (still present until anonymized), or via support exchange. Once verified, operator inserts a new `tenant_identities` row by hand or invites the user to add an identity via a one-time recovery link (generated by ops tooling).

---
## 8. Data Model

### 8.1 Database setup

- PostgreSQL 16 from the official Ubuntu repo.
- Extensions: `pgvector` ≥ 0.7.0, `pgcrypto`, `pg_trgm`.
- Roles:
  - `mem_app` — used by app processes; CRUD on tenant tables, INSERT on `audit_log`.
  - `mem_maint` — used by maintenance jobs and migrations; full access.
- Tuning (`postgresql.conf` fragment for t4g.medium 4 GB):
  - `shared_buffers = 1GB`
  - `effective_cache_size = 2GB`
  - `work_mem = 32MB`
  - `maintenance_work_mem = 256MB`
  - `max_connections = 100`
  - `wal_compression = on`
  - `synchronous_commit = on`
- Listen address: `127.0.0.1` only.
- `pg_hba.conf` enforces `scram-sha-256` for both roles.

### 8.2 Migrations

Alembic-managed. Initial migration `0001_initial_schema` contains all DDL below. Subsequent migrations only ADD; no destructive migrations in v1.

### 8.3 Full DDL

```sql
-- =============================================================================
-- mem-mcp/migrations/0001_initial_schema
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- =============================================================================
-- TENANTS
-- =============================================================================
CREATE TABLE tenants (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email                       TEXT UNIQUE NOT NULL,           -- canonical contact
    display_name                TEXT,
    status                      TEXT NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active','suspended','pending_deletion','deleted')),
    tier                        TEXT NOT NULL DEFAULT 'premium'
                                CHECK (tier IN ('standard','premium','gold','platinum')),
    limits_override             JSONB,                          -- nullable; per-tenant overrides
    retention_days              INT NOT NULL DEFAULT 365 CHECK (retention_days BETWEEN 7 AND 3650),
    deletion_requested_at       TIMESTAMPTZ,
    deletion_cancel_token_hash  TEXT,
    metadata                    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_tenants_status ON tenants(status);
CREATE INDEX idx_tenants_pending_deletion ON tenants(deletion_requested_at)
    WHERE status = 'pending_deletion';

-- =============================================================================
-- TENANT IDENTITIES (one Cognito user = one row; multiple per tenant)
-- =============================================================================
CREATE TABLE tenant_identities (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    cognito_sub         TEXT UNIQUE NOT NULL,
    cognito_username    TEXT,                                   -- best-effort
    provider            TEXT NOT NULL CHECK (provider IN ('google','github','cognito')),
    provider_user_id    TEXT,                                   -- IdP's stable id
    email               TEXT NOT NULL,
    is_primary          BOOLEAN NOT NULL DEFAULT false,
    linked_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ
);
CREATE INDEX idx_identities_tenant ON tenant_identities(tenant_id);
CREATE UNIQUE INDEX idx_identities_one_primary
    ON tenant_identities(tenant_id) WHERE is_primary;

-- =============================================================================
-- INVITED EMAILS (closed beta allowlist)
-- =============================================================================
CREATE TABLE invited_emails (
    email           TEXT PRIMARY KEY,
    invited_by      TEXT,
    invited_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at     TIMESTAMPTZ,
    notes           TEXT
);

-- =============================================================================
-- ALLOWED SOFTWARE (DCR allowlist; with future-ready review states)
-- =============================================================================
CREATE TABLE allowed_software (
    software_id     TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    vendor          TEXT NOT NULL,
    verified        BOOLEAN NOT NULL DEFAULT false,
    notes           TEXT,
    status          TEXT NOT NULL DEFAULT 'allowed'
                    CHECK (status IN ('allowed','blocked','pending_review','revoked')),
    added_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by        TEXT,
    review_payload  JSONB                                       -- v2: agent rationale
);

-- Seed rows are inserted by data migration 0002_seed_allowed_software.

-- =============================================================================
-- OAUTH CLIENTS (mirrors Cognito clients with our metadata)
-- =============================================================================
CREATE TABLE oauth_clients (
    id                              TEXT PRIMARY KEY,           -- Cognito ClientId
    tenant_id                       UUID REFERENCES tenants(id) ON DELETE SET NULL,
                                                                -- NULL until first user auth
    software_id                     TEXT REFERENCES allowed_software(software_id),
    client_name                     TEXT,
    redirect_uris                   TEXT[] NOT NULL,
    scope                           TEXT NOT NULL,
    registration_payload            JSONB NOT NULL,             -- raw RFC 7591 input
    registration_access_token_hash  TEXT,
    review_status                   TEXT NOT NULL DEFAULT 'auto_allowed'
                                    CHECK (review_status IN ('auto_allowed','pending_review',
                                          'agent_approved','agent_rejected',
                                          'human_approved','human_rejected')),
    review_notes                    JSONB,
    disabled                        BOOLEAN NOT NULL DEFAULT false,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at                    TIMESTAMPTZ,
    deleted_at                      TIMESTAMPTZ
);
CREATE INDEX idx_oauth_clients_tenant ON oauth_clients(tenant_id);
CREATE INDEX idx_oauth_clients_software ON oauth_clients(software_id);

-- =============================================================================
-- OAUTH CONSENTS (per tenant × client × scopes)
-- =============================================================================
CREATE TABLE oauth_consents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    client_id       TEXT NOT NULL REFERENCES oauth_clients(id) ON DELETE CASCADE,
    scopes          TEXT NOT NULL,                              -- canonical "scope1 scope2"
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ,
    UNIQUE (tenant_id, client_id)
);
CREATE INDEX idx_consents_tenant ON oauth_consents(tenant_id);

-- =============================================================================
-- MEMORIES (the main content table)
-- =============================================================================
CREATE TABLE memories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    content         TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 32768),
    content_hash    TEXT NOT NULL,                              -- SHA-256 of normalized content
    content_tsv     TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    embedding       VECTOR(1024) NOT NULL,                      -- Titan Embed v2
    embedding_norm  REAL,                                       -- for diagnostics
    source_client_id TEXT REFERENCES oauth_clients(id),         -- which OAuth client wrote it
    source_kind     TEXT NOT NULL CHECK (source_kind IN
                    ('claude_code','claude_chat','chatgpt','api','backfill','web_ui')),
    type            TEXT NOT NULL DEFAULT 'note'
                    CHECK (type IN ('note','decision','fact','snippet','question')),
    tags            TEXT[] NOT NULL DEFAULT '{}',
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Versioning (for decision/fact types)
    version         INT NOT NULL DEFAULT 1,
    supersedes      UUID REFERENCES memories(id),               -- this row replaces that one
    superseded_by   UUID REFERENCES memories(id),               -- backref; set when newer version created
    is_current      BOOLEAN NOT NULL DEFAULT true,              -- current version visible to search
    -- Lifecycle
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE memories FORCE ROW LEVEL SECURITY;

CREATE POLICY memories_tenant_isolation ON memories
    USING      (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

CREATE INDEX idx_memories_tenant_active
    ON memories(tenant_id, created_at DESC)
    WHERE deleted_at IS NULL AND is_current = true;
CREATE INDEX idx_memories_tags
    ON memories USING GIN(tags) WHERE deleted_at IS NULL AND is_current = true;
CREATE INDEX idx_memories_tsv
    ON memories USING GIN(content_tsv) WHERE deleted_at IS NULL AND is_current = true;
CREATE INDEX idx_memories_embedding
    ON memories USING hnsw (embedding vector_cosine_ops)
    WITH (m=16, ef_construction=64);
CREATE INDEX idx_memories_hash
    ON memories(tenant_id, content_hash)
    WHERE deleted_at IS NULL AND is_current = true;
CREATE INDEX idx_memories_supersedes
    ON memories(supersedes) WHERE supersedes IS NOT NULL;
CREATE INDEX idx_memories_type
    ON memories(tenant_id, type) WHERE deleted_at IS NULL AND is_current = true;

-- =============================================================================
-- TENANT DAILY USAGE (quota tracking)
-- =============================================================================
CREATE TABLE tenant_daily_usage (
    tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    usage_date     DATE NOT NULL,
    embed_tokens   BIGINT NOT NULL DEFAULT 0,
    writes_count   INT NOT NULL DEFAULT 0,
    reads_count    INT NOT NULL DEFAULT 0,
    deletes_count  INT NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, usage_date)
);

ALTER TABLE tenant_daily_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_daily_usage FORCE ROW LEVEL SECURITY;
CREATE POLICY usage_tenant_isolation ON tenant_daily_usage
    USING      (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- =============================================================================
-- RATE LIMITS (per-tenant + per-IP, sliding window)
-- =============================================================================
CREATE TABLE rate_limits (
    key             TEXT PRIMARY KEY,                           -- e.g. 'tenant:<uuid>:writes', 'ip:<addr>:dcr'
    bucket_start    TIMESTAMPTZ NOT NULL,
    count           INT NOT NULL DEFAULT 0
);

-- =============================================================================
-- AUDIT LOG (append-only)
-- =============================================================================
CREATE TABLE audit_log (
    id                  BIGSERIAL PRIMARY KEY,
    tenant_id           UUID,                                   -- nullable (pre-auth events)
    actor_client_id     TEXT,
    actor_identity_id   UUID REFERENCES tenant_identities(id),
    action              TEXT NOT NULL,
    target_id           UUID,
    target_kind         TEXT,                                   -- e.g. 'memory', 'identity', 'client'
    ip_address          INET,
    user_agent          TEXT,
    request_id          TEXT,
    result              TEXT NOT NULL CHECK (result IN ('success','denied','error')),
    error_code          TEXT,
    details             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_tenant_time ON audit_log(tenant_id, created_at DESC);
CREATE INDEX idx_audit_action_time ON audit_log(action, created_at DESC);
CREATE INDEX idx_audit_request_id ON audit_log(request_id);

-- =============================================================================
-- LINK STATE (signed link-flow state for cross-IdP linking)
-- =============================================================================
CREATE TABLE link_state (
    nonce           TEXT PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    expires_at      TIMESTAMPTZ NOT NULL,
    consumed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_link_state_expires ON link_state(expires_at);

-- =============================================================================
-- WEB SESSIONS (for the admin UI; opaque session ids stored hashed)
-- =============================================================================
CREATE TABLE web_sessions (
    session_hash    TEXT PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    identity_id     UUID NOT NULL REFERENCES tenant_identities(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    user_agent      TEXT,
    ip_address      INET,
    revoked_at      TIMESTAMPTZ
);
CREATE INDEX idx_sessions_tenant ON web_sessions(tenant_id);
CREATE INDEX idx_sessions_expires ON web_sessions(expires_at);

-- =============================================================================
-- FEEDBACK (memory.feedback tool)
-- =============================================================================
CREATE TABLE feedback (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    client_id       TEXT REFERENCES oauth_clients(id),
    text            TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 4096),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_feedback_tenant_time ON feedback(tenant_id, created_at DESC);

-- =============================================================================
-- ROLES & GRANTS
-- =============================================================================
-- Roles created out-of-band (psql script run as superuser).
-- mem_app:
GRANT SELECT, INSERT, UPDATE, DELETE ON tenants                  TO mem_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_identities        TO mem_app;
GRANT SELECT                          ON invited_emails          TO mem_app;
GRANT UPDATE (consumed_at)            ON invited_emails          TO mem_app;
GRANT SELECT                          ON allowed_software        TO mem_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON oauth_clients            TO mem_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON oauth_consents           TO mem_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON memories                 TO mem_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_daily_usage       TO mem_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON rate_limits              TO mem_app;
GRANT INSERT                          ON audit_log               TO mem_app;
GRANT USAGE                           ON SEQUENCE audit_log_id_seq TO mem_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON link_state               TO mem_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON web_sessions             TO mem_app;
GRANT SELECT, INSERT                  ON feedback                TO mem_app;

-- mem_maint: ALL on everything (used by retention, backups, audit anonymization).
```

### 8.4 Data migration (seed data)

```sql
-- 0002_seed_allowed_software.sql

INSERT INTO allowed_software (software_id, display_name, vendor, verified, status, notes) VALUES
('claude-code',  'Claude Code',           'Anthropic', true, 'allowed', 'Anthropic CLI agent'),
('claude-ai',    'Claude.ai (web/desktop/mobile)', 'Anthropic', true, 'allowed', 'Anthropic chat surfaces'),
('chatgpt',      'ChatGPT (developer connectors)','OpenAI',     true, 'allowed', 'OpenAI MCP'),
-- Below intentionally blocked at v1:
('cursor',       'Cursor',                'Anysphere', true, 'blocked', 'Reachable by user request'),
('perplexity',   'Perplexity',            'Perplexity AI', true, 'blocked', 'Not in v1 scope');
```

`software_id` strings are matched against the value clients send in their DCR request. Claude Code is known to send `software_id: "claude-code"`; verify the actual values for Claude.ai and ChatGPT against live DCR captures before launch.

### 8.5 Schema notes & gotchas

- **`is_current` flag**: set to false when a newer version supersedes; index predicates use it. Search and list filters MUST include `is_current = true`.
- **`content_hash` normalization**: lower-cased, whitespace-collapsed, trailing-stripped before hashing. Centralize in `mem_mcp.text.normalize_for_hash`.
- **`embedding_norm`** stored for diagnostics; should be ~1.0 since Titan v2 is normalized.
- **HNSW `ef_construction`** at 64 is a balanced default; increase to 200 if recall feels low after dogfooding.
- **GIN indexes on tsv and tags** are partial (predicate `WHERE deleted_at IS NULL AND is_current = true`) to keep them small.
- **Tenant deletion cascade**: ON DELETE CASCADE from `tenants.id` propagates to `memories`, `tenant_identities`, etc. Order matters: detach `oauth_clients.tenant_id` (SET NULL) so audit references survive.

---
## 9. MCP Server: Transport, Tools, Behaviors

### 9.1 Transport

- **NFR-9.1.1** Streamable HTTP per MCP 2025-06-18.
- **NFR-9.1.2** Single endpoint `POST /mcp` for JSON-RPC 2.0.
- **NFR-9.1.3** SSE responses (`text/event-stream`) when emitting multiple messages; otherwise `application/json`.
- **NFR-9.1.4** `Origin` header validated against allowlist when present; CORS responses do not use `*` with credentials.
- **NFR-9.1.5** Bearer JWT required on all `/mcp` requests; missing/invalid → 401 with `WWW-Authenticate: Bearer realm="mem-mcp", resource_metadata="https://mem.<domain>/.well-known/oauth-protected-resource"`.
- **NFR-9.1.6** Optional `Mcp-Session-Id` header tolerated; v1 is stateless.

### 9.2 Tool registry

| Tool | Required scope | Description |
|---|---|---|
| `memory.write` | `memory.write` | Store a new memory (or merge into existing duplicate) |
| `memory.search` | `memory.read` | Hybrid retrieval |
| `memory.get` | `memory.read` | Fetch one memory by id (current version + history if requested) |
| `memory.list` | `memory.read` | Paginated listing with filters |
| `memory.update` | `memory.write` | Update content/tags/type |
| `memory.delete` | `memory.write` | Soft-delete |
| `memory.undelete` | `memory.write` | Restore from soft-delete (within 30d window) |
| `memory.supersede` | `memory.write` | Explicitly mark a → b supersedence |
| `memory.export` | `memory.admin` | Export all tenant data as JSON |
| `memory.stats` | `memory.read` | Counts by tag/type/recency |
| `memory.feedback` | `memory.write` | Beta feedback channel |

### 9.3 Tool input/output specifications

#### 9.3.1 `memory.write`

```jsonc
// Input
{
  "type": "object",
  "required": ["content"],
  "properties": {
    "content":  { "type": "string", "minLength": 1, "maxLength": 32768 },
    "type":     { "enum": ["note","decision","fact","snippet","question"], "default": "note" },
    "tags":     { "type": "array", "items": { "type": "string", "minLength": 1, "maxLength": 64,
                                               "pattern": "^[a-zA-Z0-9_:.-]+$" },
                  "maxItems": 32, "uniqueItems": true },
    "metadata": { "type": "object", "additionalProperties": true },
    "supersedes": { "type": "string", "format": "uuid" },
    "force_new":  { "type": "boolean", "default": false,
                    "description": "If true, skip dedupe and always create a new memory" }
  },
  "additionalProperties": false
}

// Output
{
  "id": "uuid",
  "version": 1,
  "deduped": false,                  // true if merged into existing memory
  "merged_into": "uuid|null",        // present when deduped=true
  "created_at": "ISO-8601",
  "request_id": "..."
}
```

**Behavior (FR-9.3.1.x):**

- **FR-9.3.1.1** Validate input; reject invalid with JSON-RPC `-32602`.
- **FR-9.3.1.2** Reject if tenant `status != 'active'`.
- **FR-9.3.1.3** Check write quota (per-min and per-day). Reject with `quota_exceeded` JSON-RPC `-32000` if exceeded.
- **FR-9.3.1.4** Check daily embed_tokens budget. Reject if exceeded.
- **FR-9.3.1.5** Compute `content_hash` from normalized content.
- **FR-9.3.1.6** Compute embedding via Bedrock Titan v2. Token count returned by Bedrock recorded.
- **FR-9.3.1.7** Dedupe (unless `force_new=true`):
  - Hash match against last 1000 memories (indexed by hash) → if found, treat as dup.
  - Else cosine similarity > 0.95 against last 50 memories of same `type` → if found, treat as dup.
- **FR-9.3.1.8** On dup: UPDATE existing row's `tags = tags ∪ new_tags`, `updated_at = now()`. Return `deduped=true, merged_into=<existing_id>`. Audit `memory.dedupe_merged`.
- **FR-9.3.1.9** On supersedes: validate that target memory exists, belongs to tenant, type ∈ {decision,fact}. Insert new memory with `supersedes`, `version = old.version + 1`. UPDATE old: `is_current = false`, `superseded_by = new.id`. Audit `memory.supersede`.
- **FR-9.3.1.10** Otherwise INSERT memory.
- **FR-9.3.1.11** Increment `tenant_daily_usage.embed_tokens, writes_count`.
- **FR-9.3.1.12** Audit `memory.write` with `target_id=memory.id`.
- **FR-9.3.1.13** All of the above in one transaction.

#### 9.3.2 `memory.search`

```jsonc
// Input
{
  "type": "object",
  "required": ["query"],
  "properties": {
    "query":           { "type": "string", "minLength": 1, "maxLength": 2048 },
    "tags":            { "type": "array", "items": { "type": "string" }, "maxItems": 16 },
    "type":            { "enum": ["note","decision","fact","snippet","question"] },
    "since":           { "type": "string", "format": "date-time" },
    "until":           { "type": "string", "format": "date-time" },
    "limit":           { "type": "integer", "minimum": 1, "maximum": 50, "default": 10 },
    "include_history": { "type": "boolean", "default": false,
                         "description": "If true, return non-current versions too" },
    "recency_lambda":  { "type": "number", "minimum": 0, "maximum": 1,
                         "description": "Override default recency decay" }
  },
  "additionalProperties": false
}

// Output
{
  "results": [
    {
      "id": "uuid",
      "content": "...",
      "type": "decision",
      "tags": ["..."],
      "version": 2,
      "created_at": "...",
      "updated_at": "...",
      "score": 0.873,
      "scores_breakdown": { "semantic": 0.91, "keyword": 0.42, "recency_factor": 0.95 }
    }
  ],
  "query_embedding_tokens": 12,
  "request_id": "..."
}
```

**Behavior:**

- **FR-9.3.2.1** Validate; reject empty `query`.
- **FR-9.3.2.2** Read quota check.
- **FR-9.3.2.3** Embed query via Bedrock; record tokens.
- **FR-9.3.2.4** Run hybrid query (§10.3) with tenant's tier-based limits and per-type recency_lambda default (override if input provided).
- **FR-9.3.2.5** Return at most `limit` results, sorted by `score` descending.
- **FR-9.3.2.6** Audit `memory.search` with summary in details (no content).

#### 9.3.3 `memory.get`

```jsonc
// Input
{
  "type": "object",
  "required": ["id"],
  "properties": {
    "id":              { "type": "string", "format": "uuid" },
    "include_history": { "type": "boolean", "default": false }
  }
}

// Output
{
  "memory": { /* full memory object */ },
  "history": [ /* prior versions if include_history=true */ ]
}
```

#### 9.3.4 `memory.list`

```jsonc
// Input
{
  "type": "object",
  "properties": {
    "tags":         { "type": "array", "items": { "type": "string" } },
    "type":         { "enum": ["note","decision","fact","snippet","question"] },
    "since":        { "type": "string", "format": "date-time" },
    "until":        { "type": "string", "format": "date-time" },
    "include_deleted": { "type": "boolean", "default": false },
    "include_history": { "type": "boolean", "default": false },
    "order_by":     { "enum": ["created_at","updated_at"], "default": "created_at" },
    "order":        { "enum": ["asc","desc"], "default": "desc" },
    "limit":        { "type": "integer", "minimum": 1, "maximum": 100, "default": 25 },
    "cursor":       { "type": "string" }
  }
}

// Output
{ "results": [...], "next_cursor": "...|null" }
```

#### 9.3.5 `memory.update`

```jsonc
// Input
{
  "type": "object",
  "required": ["id"],
  "properties": {
    "id":      { "type": "string", "format": "uuid" },
    "content": { "type": "string", "minLength": 1, "maxLength": 32768 },
    "type":    { "enum": ["note","decision","fact","snippet","question"] },
    "tags":    { "type": "array" },
    "metadata":{ "type": "object" },
    "tags_op": { "enum": ["replace","add","remove"], "default": "replace" }
  }
}

// Output
{ "id": "...", "version": 2, "is_new_version": true|false }
```

**Behavior:**

- **FR-9.3.5.1** If `type` ∈ {decision, fact} AND content is changing: create a new version (insert new row, mark old `is_current=false`, link via `supersedes`/`superseded_by`).
- **FR-9.3.5.2** If `type` ∈ {note, snippet, question} OR only tags/metadata changing: update in place, bump `updated_at`.
- **FR-9.3.5.3** Re-embed if content changed.
- **FR-9.3.5.4** If `type` is being changed from non-versioned to versioned (e.g. note → decision), treat as new version-1 of a versioned memory.

#### 9.3.6 `memory.delete`

```jsonc
// Input { "id": "uuid" }
// Output { "id": "...", "deleted_at": "..." }
```

- **FR-9.3.6.1** Soft-delete: set `deleted_at = now()`. Memory recoverable for 30 days.
- **FR-9.3.6.2** Versioned memories: delete only the current version unless `cascade=true` (default false; cascade requires `memory.admin` scope).

#### 9.3.7 `memory.undelete`

```jsonc
// Input { "id": "uuid" }
// Output { "id": "...", "deleted_at": null }
```

- **FR-9.3.7.1** Allowed only if `now() - deleted_at < 30 days`. Else 404 + `cannot_undelete_after_grace_period`.
- **FR-9.3.7.2** Ensure no current version exists for the same versioned chain (prevent two `is_current` siblings).

#### 9.3.8 `memory.supersede`

```jsonc
// Input { "old_id": "uuid", "new_id": "uuid" }
```

- **FR-9.3.8.1** Validate both belong to tenant, are same type ∈ {decision, fact}.
- **FR-9.3.8.2** Mark `old.is_current=false, old.superseded_by=new.id`; `new.supersedes=old.id`.

#### 9.3.9 `memory.export`

- **FR-9.3.9.1** Returns full JSON dump of all memories (current + history) and audit rows for the tenant. Used to fulfill DPDP "right to access."
- **FR-9.3.9.2** Requires `memory.admin` scope. Web UI version reuses this same endpoint.

#### 9.3.10 `memory.stats`

```jsonc
// Output
{
  "total_memories": 1234,
  "by_type":      { "note": 800, "decision": 200, "fact": 150, "snippet": 80, "question": 4 },
  "top_tags":     [ {"tag":"ew", "count": 234}, ... ],
  "oldest":       "ISO-8601",
  "newest":       "ISO-8601",
  "writes_today": 12,
  "reads_today":  87,
  "embed_tokens_today": 8421,
  "quota": {
    "tier": "premium",
    "memories_limit": 25000,
    "embed_tokens_daily_limit": 100000,
    "writes_per_minute_limit": 120,
    "reads_per_minute_limit": 600
  }
}
```

#### 9.3.11 `memory.feedback`

```jsonc
// Input
{ "text": "string max 4096", "metadata": { "...": "..." } }
// Output
{ "id": "uuid", "received_at": "..." }
```

- **FR-9.3.11.1** Stores in `feedback` table; non-blocking (no embedding).
- **FR-9.3.11.2** Triggers a daily summary email to the operator.

### 9.4 Error model

- **NFR-9.4.1** Validation errors: JSON-RPC `-32602` with `data.errors = [{path, message}]`.
- **NFR-9.4.2** Auth errors: HTTP 401 (Bearer middleware), with WWW-Authenticate header.
- **NFR-9.4.3** Authorization (scope) errors: JSON-RPC `-32000`, `data.code = "insufficient_scope"`, `data.required_scopes = [...]`.
- **NFR-9.4.4** Quota errors: JSON-RPC `-32000`, `data.code = "quota_exceeded"`, `data.quota = "memories_count|embed_tokens_daily|writes_per_minute"`, `data.tier`, `data.reset_at`, `data.upgrade_url`.
- **NFR-9.4.5** Rate-limit errors: HTTP 429 with `Retry-After`; for tools, JSON-RPC `-32000` with `data.code = "rate_limited"`.
- **NFR-9.4.6** Tenant suspended/pending_deletion: HTTP 403 with JSON-RPC `data.code = "account_suspended" | "account_deletion_pending"`.
- **NFR-9.4.7** Server errors: JSON-RPC `-32603` with safe message; full stack in CloudWatch only.
- **NFR-9.4.8** Upstream Bedrock failures: JSON-RPC `-32000`, `data.code = "embedding_unavailable"`, `data.retry_after = N`.

### 9.5 Idempotency

- **FR-9.5.1** Tool calls support optional `Idempotency-Key` header on `POST /mcp`. When provided, mem-mcp records (key, request_hash, response) in a small in-memory LRU cache (15-minute TTL) and returns cached response on retry. Reduces double-writes from agent retries.

### 9.6 Telemetry headers (response)

- **NFR-9.6.1** Every response includes `X-Request-Id` and `X-Mem-Quota-Used: writes=12/120` style header for visibility.

---

## 10. Memory Pipeline Internals

### 10.1 Embedding service

- **NFR-10.1.1** Bedrock `amazon.titan-embed-text-v2:0`, region `ap-south-1`, `dimensions=1024`, `normalize=true`.
- **NFR-10.1.2** Call wrapped with Tenacity: 3 attempts, exponential backoff (200ms, 800ms, 3.2s), retry on transient (`ThrottlingException`, `ServiceUnavailable`, `InternalServerError`).
- **NFR-10.1.3** On final failure: return `embedding_unavailable` to caller (do NOT silently store without embedding).
- **NFR-10.1.4** Bedrock call duration metric emitted (`mem_mcp.embed.duration_ms`).
- **NFR-10.1.5** Token count from response stored in `tenant_daily_usage.embed_tokens`.

### 10.2 Content normalization

- **NFR-10.2.1** Hash input = `unicodedata.normalize("NFKC", text).strip().lower()` with internal whitespace collapsed to single spaces. Centralized in `mem_mcp.text.normalize_for_hash`.
- **NFR-10.2.2** Embedding input = original content (NOT normalized; preserve casing/punctuation for embedding semantics).

### 10.3 Hybrid retrieval query (canonical SQL)

```sql
-- Inputs (named params for clarity in code; positional in DB):
--   :qvec        VECTOR(1024)        query embedding
--   :qtxt        TEXT                query text (for FTS)
--   :tenant_id   UUID                (also via SET LOCAL)
--   :type        TEXT                NULL = all
--   :tags        TEXT[]              NULL = all
--   :since       TIMESTAMPTZ         NULL = no lower bound
--   :until       TIMESTAMPTZ         NULL = no upper bound
--   :limit       INT
--   :recency_lambda  REAL
--   :w_sem       REAL                default 0.7
--   :w_kw        REAL                default 0.3

WITH semantic AS (
    SELECT id, 1 - (embedding <=> :qvec) AS sem_score
    FROM memories
    WHERE tenant_id = :tenant_id
      AND deleted_at IS NULL
      AND is_current = true
      AND (:type IS NULL OR type = :type)
      AND (:tags IS NULL OR tags && :tags)
      AND (:since IS NULL OR created_at >= :since)
      AND (:until IS NULL OR created_at <= :until)
    ORDER BY embedding <=> :qvec
    LIMIT 50
),
keyword AS (
    SELECT id, ts_rank_cd(content_tsv, q) AS kw_score
    FROM memories, plainto_tsquery('english', :qtxt) q
    WHERE tenant_id = :tenant_id
      AND deleted_at IS NULL
      AND is_current = true
      AND content_tsv @@ q
      AND (:type IS NULL OR type = :type)
      AND (:tags IS NULL OR tags && :tags)
      AND (:since IS NULL OR created_at >= :since)
      AND (:until IS NULL OR created_at <= :until)
    ORDER BY kw_score DESC
    LIMIT 50
),
combined AS (
    SELECT m.id,
           COALESCE(s.sem_score, 0) AS sem_score,
           COALESCE(k.kw_score, 0)  AS kw_score,
           EXTRACT(EPOCH FROM (now() - m.created_at)) / 86400.0 AS age_days
    FROM memories m
    LEFT JOIN semantic s ON s.id = m.id
    LEFT JOIN keyword  k ON k.id = m.id
    WHERE m.id IN (SELECT id FROM semantic UNION SELECT id FROM keyword)
      AND m.tenant_id = :tenant_id
)
SELECT m.id, m.content, m.type, m.tags, m.version,
       m.created_at, m.updated_at,
       c.sem_score, c.kw_score,
       exp(-:recency_lambda * c.age_days) AS recency_factor,
       (
           :w_sem * c.sem_score
         + :w_kw  * (c.kw_score / GREATEST((SELECT MAX(kw_score) FROM keyword), 0.0001))
       ) * exp(-:recency_lambda * c.age_days) AS score
FROM combined c
JOIN memories m ON m.id = c.id
ORDER BY score DESC
LIMIT :limit;
```

### 10.4 Default recency_lambda by type

(decision/fact decay slowly; notes/snippets decay quickly)

| type | default lambda | half-life (days) |
|---|---|---|
| decision | 0.0019 | 365 |
| fact     | 0.0019 | 365 |
| note     | 0.05   | 14 |
| snippet  | 0.10   | 7 |
| question | 0.05   | 14 |

These live in app config (`mem_mcp/config.py:RECENCY_LAMBDA_BY_TYPE`), not the DB. Tunable via env var or a future settings table.

### 10.5 Dedupe pipeline

```
Input: tenant_id, content, type, tags, force_new

if force_new:  insert as new, return
hash = normalize_for_hash(content); SHA-256
existing = SELECT id, tags FROM memories
           WHERE tenant_id=? AND content_hash=hash
           AND deleted_at IS NULL AND is_current = true LIMIT 1
if existing:
    UPDATE memories SET tags = tags ∪ new_tags, updated_at = now() WHERE id = existing.id
    audit memory.dedupe_merged (kind=hash)
    return {deduped: true, merged_into: existing.id}

embedding = bedrock.embed(content)

near = SELECT id, 1 - (embedding <=> ?) AS sim, tags
       FROM memories
       WHERE tenant_id=? AND type=? AND deleted_at IS NULL AND is_current = true
       ORDER BY embedding <=> ? LIMIT 50
top = first row of near where sim > 0.95

if top:
    UPDATE memories SET tags = tags ∪ new_tags, updated_at = now() WHERE id = top.id
    audit memory.dedupe_merged (kind=embedding, sim=...)
    return {deduped: true, merged_into: top.id}

INSERT new memory; return {deduped: false}
```

### 10.6 Versioning rules

- **FR-10.6.1** Versioned types: `decision`, `fact`. Non-versioned: `note`, `snippet`, `question`.
- **FR-10.6.2** Update of versioned memory's `content` creates a new row; old row retains its content (history preserved).
- **FR-10.6.3** Search returns only `is_current=true` rows by default. `include_history=true` returns prior versions too.
- **FR-10.6.4** Deletion of a current version cascades to next-most-recent? **No** — deletion of the current version sets `is_current=false` on it AND `is_current=true` on the most recent prior version with `deleted_at IS NULL`. This avoids "delete current → memory disappears entirely from search even though older versions exist."
- **FR-10.6.5** Hard delete of a memory chain is allowed by the tenant via the web UI ("Delete all versions of this decision").

---

## 11. Quotas, Tiers, Abuse Controls

### 11.1 Tier definitions (config in `mem_mcp/quotas.py`)

```python
TIERS = {
  "standard": {
    "memories_limit": 5_000,
    "embed_tokens_daily": 25_000,
    "writes_per_minute": 60,
    "reads_per_minute": 300,
  },
  "premium": {                              # default for beta
    "memories_limit": 25_000,
    "embed_tokens_daily": 100_000,
    "writes_per_minute": 120,
    "reads_per_minute": 600,
  },
  "gold": {
    "memories_limit": 100_000,
    "embed_tokens_daily": 500_000,
    "writes_per_minute": 240,
    "reads_per_minute": 1_200,
  },
  "platinum": {
    "memories_limit": 500_000,
    "embed_tokens_daily": 2_000_000,
    "writes_per_minute": 600,
    "reads_per_minute": 3_000,
  },
}
```

`tenants.limits_override` (JSONB) shadows specific keys when present.

### 11.2 Quota enforcement

- **FR-11.2.1** `memory.write` checks: memories_count < limit (otherwise `quota_exceeded` with `quota=memories_count`).
- **FR-11.2.2** `memory.write` checks: today's `embed_tokens + estimated_tokens <= daily_limit`. Estimate = `len(content)/4` if Bedrock not yet called.
- **FR-11.2.3** Per-minute rate limits enforced via sliding-window in `rate_limits` table or in-memory token bucket (in-memory simpler; OK for single-VM v1).
- **FR-11.2.4** Quota errors are 429 (HTTP) for raw rate limits; structured JSON-RPC `-32000` for daily/count quotas.

### 11.3 Global abuse caps (across all tenants)

Independent of per-tenant quotas, the system has hard ceilings to limit blast radius:

- **NFR-11.3.1** `/oauth/register` global limit: 100 calls/day across all IPs.
- **NFR-11.3.2** `/oauth/register` per-IP: 5 calls/hour.
- **NFR-11.3.3** New tenant creation (PreSignUp Lambda allows): 50/day across all IPs (alarm + circuit breaker; closed beta is way under this).
- **NFR-11.3.4** Bedrock invocation: hard ceiling of 1M tokens/day across all tenants in v1 — circuit breaker disables embedding when hit.

### 11.4 Daily reset

- **FR-11.4.1** `tenant_daily_usage` rows are keyed by `usage_date` in IST (Asia/Kolkata). New row created on first call after midnight IST.
- **FR-11.4.2** Quota error responses include `reset_at` set to next midnight IST.

### 11.5 Tier upgrade path (v2)

Schema already supports — `tenants.tier` is updatable. v2 adds Stripe webhook → updates tier; sends notification email.

---
## 12. Web Application (Admin UI)

### 12.1 Purpose & scope

A self-service web app at `app.<your-domain>` for:

- Onboarding (first sign-in lands here).
- Identity management (link, unlink, set primary).
- Memory management (browse, search, view, edit, delete, undelete).
- Stats dashboard.
- Settings: tier (read-only in v1), retention days, beta feedback.
- Connected applications (list of OAuth clients with revoke).
- DPDP operations: export, delete account.
- Skill installation guidance (with copyable connector URL).

### 12.2 Stack

- **Framework**: Next.js 15 (App Router), TypeScript, server components where sensible.
- **Auth**: Cognito Authorization Code with PKCE; session = httpOnly cookie containing opaque session id; server-side sessions in `web_sessions` table.
- **Styling**: Tailwind CSS + a small set of headless components (Radix UI). No design system tickets in v1; functional minimalism.
- **API**: Direct backend = a separate set of routes on the same `mem-mcp` FastAPI process under `/api/web/*`, protected by web-session auth (NOT MCP Bearer JWT).
- **Hosting**: same VM, port 8081, served by Caddy on `app.<your-domain>`.

### 12.3 Pages (FRs)

#### 12.3.1 `/` — Landing

- Public; explains the service in 2-3 sentences.
- "Sign in" button → Cognito Hosted UI.
- After auth callback: redirect to `/dashboard` if tenant exists, else `/welcome`.

#### 12.3.2 `/welcome` — First-time onboarding

- **FR-12.3.2.1** Shown only on first sign-in (when fresh `tenants` row was just created).
- **FR-12.3.2.2** Welcome message + steps to connect AI clients.
- **FR-12.3.2.3** Each AI client has a card: copyable MCP URL (`https://mem.<your-domain>/mcp`), copyable command (`claude mcp add ...`), download links for skill bundles.
- **FR-12.3.2.4** Final step: "Try it now" button that just dismisses and goes to `/dashboard`.

#### 12.3.3 `/dashboard` — Stats overview

- Counts (total, by type), top tags, recent activity timeline.
- Quota usage bars: memories used/limit, embed tokens today/limit.
- Tier badge.

#### 12.3.4 `/memories` — Memory browser

- Table view: type, content preview (~120 chars), tags (chips), created_at, version.
- Filters: type, tag (multi-select), date range, search box (uses `memory.search` semantically).
- Pagination via cursor.
- Row click → `/memories/{id}` detail view.
- Bulk actions (v1 minimal): delete selected.

#### 12.3.5 `/memories/{id}` — Memory detail

- Full content, all metadata.
- For versioned memories: history timeline (older versions linked).
- Edit (opens form), Delete (confirm), Undelete (if soft-deleted).
- Audit trail: last 20 audit rows referencing this memory.

#### 12.3.6 `/settings` — Account settings

- Profile: email, display name (editable).
- Tier badge (read-only in v1, "upgrade requires v2 billing").
- Retention days (input with validation 7–3650).
- "Close account" button → confirmation flow per §7.7.

#### 12.3.7 `/settings/identities` — Identity management

- List all linked identities: provider, email at IdP, primary badge, last seen.
- Buttons per row: Promote to primary, Unlink (with §7.5 rules).
- "Add another identity" button → starts link flow per §7.4.

#### 12.3.8 `/settings/applications` — Connected applications

- **FR-12.3.8.1** Lists all `oauth_clients` belonging to this tenant with: client_name, software_id (with verified badge), created_at, last_used_at, last IP.
- **FR-12.3.8.2** Revoke button per row → marks client `disabled=true`, calls Cognito `DeleteUserPoolClient`, immediately invalidates all tokens for that client.

#### 12.3.9 `/settings/feedback` — Beta feedback

- Simple textarea + submit. Posts to backend; appended to `feedback` table.

#### 12.3.10 `/data/export` — DPDP export

- "Download my data" button → backend generates JSON, streams as download.
- Includes: tenants row, all tenant_identities, all memories (current + history), audit_log rows for tenant.

#### 12.3.11 `/data/delete` — Account closure flow

- Per §7.7. Multi-step confirmation, re-auth, banner during 24h cancel window.

#### 12.3.12 `/skills` — Skill installation

- Static page listing the two skills (`mem-capture`, `mem-recall`).
- Download `.skill` bundle for Claude Code. Copy-paste blocks for Claude.ai project instructions and ChatGPT custom GPT instructions.

#### 12.3.13 `/legal/privacy`, `/legal/terms`

- Static pages, plain HTML. Required for Google OAuth verification.

### 12.4 Backend API for web (`/api/web/*`)

Auth: web-session cookie. Maps session → tenant_id → uses `tenant_tx`.

| Path | Method | Purpose |
|---|---|---|
| `/api/web/me` | GET | Current tenant + identities |
| `/api/web/tenant` | PATCH | Update display_name, retention_days |
| `/api/web/identities` | GET | List identities |
| `/api/web/identities/{id}/promote` | POST | Set primary |
| `/api/web/identities/{id}` | DELETE | Unlink |
| `/api/web/identities/link/start` | POST | Generate signed link_state, return Cognito URL |
| `/api/web/identities/link/complete` | POST | Callback handler (server-side, called by /auth/callback) |
| `/api/web/clients` | GET | List oauth_clients |
| `/api/web/clients/{id}` | DELETE | Revoke |
| `/api/web/memories` | GET | List with filters |
| `/api/web/memories/{id}` | GET | Detail |
| `/api/web/memories/{id}` | PATCH | Edit |
| `/api/web/memories/{id}` | DELETE | Soft delete |
| `/api/web/memories/{id}/undelete` | POST | Restore |
| `/api/web/memories/search` | POST | Search (proxies tool) |
| `/api/web/stats` | GET | Dashboard data |
| `/api/web/feedback` | POST | Submit feedback |
| `/api/web/data/export` | GET | Stream JSON dump |
| `/api/web/data/delete` | POST | Initiate account closure |
| `/api/web/data/delete/cancel` | POST | Cancel within 24h window |
| `/auth/login` | GET | Redirect to Cognito Hosted UI |
| `/auth/callback` | GET | OAuth code exchange; create web session |
| `/auth/logout` | POST | Revoke session |

### 12.5 Web session management

- **NFR-12.5.1** Session cookie: HttpOnly, Secure, SameSite=Lax, path=/, 7-day expiry, name `mem_session`.
- **NFR-12.5.2** Session id is 32 random bytes, base64url. Hash (SHA-256) stored in `web_sessions.session_hash`.
- **NFR-12.5.3** Logout deletes session row and clears cookie.
- **NFR-12.5.4** All `/api/web/*` requests check session validity, update `last_seen_at`, enforce expiry.
- **NFR-12.5.5** Re-authentication required (Cognito step-up) for: account closure, unlink primary identity (after promotion), exports.

### 12.6 CSP & web security

- **NFR-12.6.1** CSP: `default-src 'self'; script-src 'self' 'unsafe-inline' (only for next.js inlined runtime) ; connect-src 'self' https://cognito-idp.<region>.amazonaws.com; frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://auth.<your-domain>;`.
- **NFR-12.6.2** CSRF: double-submit cookie pattern on POST/PATCH/DELETE endpoints. CSRF token in cookie + header.
- **NFR-12.6.3** Rate limit on web API: 60 req/min per session.

---

## 13. Skills & Client Integration

### 13.1 Distribution

- **FR-13.1.1** Skills hosted at `app.<your-domain>/skills` as downloadable bundles.
- **FR-13.1.2** Each invitation email includes the connector URL and a link to `/skills`.

### 13.2 `mem-capture` skill (Claude Code)

```yaml
# skills/mem-capture/SKILL.md
---
name: mem-capture
description: Stores user decisions, facts, and notable conclusions to long-term memory. Triggers when the user expresses a decision ("we'll go with X", "decided to", "plan is to"), states a fact worth remembering, or explicitly says "remember", "save this", "note that". Calls memory.write on the configured mem-mcp connector.
---

When the user states or implies a memorable item (decisions, facts, configuration choices, preferences, recurring snippets), call the `memory.write` tool on the mem-mcp connector with:
- `content`: a clear, self-contained restatement of the item (not a verbatim copy unless useful)
- `type`: one of decision | fact | snippet | note | question (pick the closest)
- `tags`: 2-6 tags including a project tag (e.g., `project:ew`) and topic tags
- `metadata`: { source: "claude-code", session_id: "..." } if available

Do NOT call memory.write for trivial chit-chat, emotional content, or sensitive information unless the user explicitly asks.

Confirm in one short sentence after writing: "Saved as a {type} memory."
```

### 13.3 `mem-recall` skill

```yaml
# skills/mem-recall/SKILL.md
---
name: mem-recall
description: Retrieves relevant prior memories when a user message references past context. Triggers on possessives ("my project", "our approach"), definite articles assuming shared reference ("the script", "that decision"), or explicit asks ("what did we decide", "remind me"). Calls memory.search on the configured mem-mcp connector before responding.
---

Before responding to messages that reference prior context, call `memory.search` with:
- `query`: the user's message verbatim, trimmed to 200 chars
- `tags`: include the active project tag if obvious from context
- `limit`: 8

Use returned context naturally; do not announce that you searched memory unless asked.

If no results return: respond normally without speculating about prior context.
```

### 13.4 Claude.ai project instructions template

A copy-paste block at `/skills` for Claude.ai users (since skills aren't always available there):

```
# Memory connector instructions

You have access to a memory store via the "mem-mcp" connector. Use it as follows:

CAPTURE: When the user states a decision, fact, or notable conclusion, OR says "remember/save/note", call mem-mcp memory.write with appropriate type (decision|fact|snippet|note|question) and 2-6 tags including a project tag (project:<name>).

RECALL: Before responding to messages that reference prior context (uses "we", "our", "the project", "what did we decide"), call mem-mcp memory.search with the user's message as query.

Do not announce memory operations. Just use them.
```

### 13.5 ChatGPT custom GPT instructions template

Equivalent block, adjusted for ChatGPT's connector phrasing.

### 13.6 Verification

- **FR-13.6.1** Skills tested with at least 3 beta users on each platform (Claude Code, Claude.ai, ChatGPT) before invitation expansion.
- **FR-13.6.2** A small eval harness (§18.5) of 20 hand-curated query/expected-result pairs exercises the recall path.

---

## 14. Operations: Deployment, Backup, Retention, Observability

### 14.1 Deployment

- **FR-14.1.1** All AWS infrastructure managed in `infra/terraform/`. State stored in S3 with DynamoDB lock table.
- **FR-14.1.2** EC2 user-data (`infra/cloud-init/user-data.yaml`) installs Caddy, Postgres 16 + extensions, Python 3.12, Node 20, awscli, CloudWatch agent, fail2ban, unattended-upgrades; clones the repo; runs `bootstrap.sh`.
- **FR-14.1.3** `bootstrap.sh` creates DB roles, runs migrations, builds the Next.js app, sets up systemd units.
- **FR-14.1.4** Subsequent deploys: SSH in, `git pull`, run `deploy.sh`. `deploy.sh` runs migrations, restarts services, waits for `/readyz` to return 200.
- **FR-14.1.5** Rollback: `git checkout <prev>; ./deploy.sh`. Migrations are forward-only; rollback assumes no schema changes since the last deploy or that they're backward-compatible.

### 14.2 Backups

- **FR-14.2.1** Nightly `pg_dump_to_s3.sh` (systemd timer at 02:30 IST):
  1. `pg_dump --format=custom --compress=9 mem_mcp` → temp file
  2. `gpg --cipher-algo AES256 --batch --passphrase-file <ssm passphrase>` → encrypted file
  3. `aws s3 cp <file> s3://mem-mcp-backups-.../db/<YYYY-MM-DD>.sql.gz.gpg`
  4. Cleanup temp file
  5. Emit CloudWatch metric `mem_mcp.backup.success` (1)
- **FR-14.2.2** Restore script `restore_from_s3.sh`: prompts for date, downloads, decrypts, `pg_restore` into a fresh DB. Documented in runbook.
- **FR-14.2.3** Quarterly restore drill: spin up secondary VM, restore latest backup, smoke test, tear down. Scheduled in operator calendar.

### 14.3 Retention jobs (systemd timers)

- **FR-14.3.1** `mem-mcp-retention-memories.timer` daily 03:00 IST: soft-delete memories past `retention_days`; hard-delete `deleted_at < now() - 30d`.
- **FR-14.3.2** `mem-mcp-retention-tokens.timer` hourly: purge expired authorization codes, refresh tokens, login tokens, link_state, web_sessions.
- **FR-14.3.3** `mem-mcp-retention-audit.timer` daily 04:00 IST: anonymize audit rows of deleted tenants past 90 days; hard-delete audit rows past 730 days.
- **FR-14.3.4** `mem-mcp-retention-deletion.timer` hourly: process `tenants` with `status='pending_deletion' AND deletion_requested_at < now() - 24h`.
- **FR-14.3.5** `mem-mcp-cleanup-clients.timer` daily: delete unused Cognito clients per FR-6.5.10.

### 14.4 CloudWatch alarms

| Alarm | Metric / source | Threshold | Action |
|---|---|---|---|
| App-5xx-rate | log filter on `/mem-mcp/app` for HTTP 5xx | > 1% over 5min | SNS → email |
| App-down | EC2 status check | failed 2 of 3 | SNS → email |
| Auth-fail-spike | log filter for 401/403 | > 50/min sustained 10min | SNS → email |
| DCR-attempts | log filter for `oauth.dcr_register` | > 20/hour | SNS → email |
| Token-reuse | log filter for `auth.token_refresh_reuse` | > 0 | SNS → page |
| Tenant-isolation-test-failure | nightly synthetic test result | failed | SNS → page |
| Disk-usage | EC2 metric | > 80% | SNS → email |
| CPU-usage | EC2 metric | > 80% sustained 15min | SNS → email |
| Backup-stale | last-backup age | > 36h | SNS → email |
| SES-bounce-rate | SES metric | > 5% | SNS → email |
| Bedrock-throttle | log filter | > 10/hour | SNS → email |
| Quota-circuit-breaker | log filter for global cap hit | > 0 | SNS → page |

### 14.5 Logging

- **NFR-14.5.1** Structured JSON to stdout. systemd → journald → CloudWatch agent → log group.
- **NFR-14.5.2** Every line includes: `timestamp, level, request_id, tenant_id?, client_id?, action, latency_ms?, result?, message`.
- **NFR-14.5.3** **Never log**: memory content, embeddings, JWTs, session ids, magic-link values, GPG passphrase. Only log lengths, hashes, IDs.
- **NFR-14.5.4** Log retention: app & web 30d, audit 90d (separate stream).

### 14.6 Operator runbooks (live in `docs/runbooks/`)

- `add_beta_user.md` — insert into `invited_emails`, send invite email.
- `suspend_tenant.md` — set status, revoke tokens, email user.
- `dpdp_export_request.md` — ops-side fulfillment (alternative to web UI export).
- `dpdp_delete_request.md` — ops-side fulfillment.
- `restore_from_backup.md` — full restore walkthrough.
- `rotate_jwt_keys.md` — rotation in Cognito (advanced security mode).
- `investigate_token_reuse.md` — incident playbook.
- `add_allowed_software.md` — add new AI client to allowlist.
- `lockout_recovery.md` — user lost all identities.
- `tenant_merge.md` — rare manual operation.

---

## 15. Threat Model

| Threat | Mitigation |
|---|---|
| Cross-tenant SELECT via SQLi in tags/query | Parameterized queries; RLS; explicit `WHERE tenant_id`. Linter enforces. |
| Cross-tenant SELECT via missed `SET LOCAL` | RLS fails closed → 0 rows. Pool leak test (§18.3). |
| Cross-tenant INSERT/UPDATE | RLS `WITH CHECK` rejects mismatched tenant_id inserts. |
| OAuth code interception | PKCE S256 mandatory (Cognito); HTTPS only; strict redirect_uri match. |
| Refresh token theft | Cognito's revocation enabled; consents revocable per client; per-IP anomaly alarm. |
| DCR abuse | Per-IP rate limit, software_id allowlist, daily global cap, audit alarm. |
| Magic-link/login-link interception | Not used (federation only). |
| Rogue MCP client claiming to be Claude Code | Allowlist by software_id (catches honest), consent screen with verified badge, connected-apps page for user-side revocation. |
| User accidentally authorizing wrong client | Custom consent screen with vendor identity, scope list. |
| Token replay after tenant suspension | Per-request tenant.status check; suspended → 403. |
| Email collision attack (signup with victim's email) | PreSignUp Lambda denies; user must own the IdP account at the IdP. |
| Account takeover via account-linking flow | Signed link_state, 10-min expiry, server-verified web session, no email-match auto-link. |
| Account takeover via unlink | Unlink-last-identity refused; primary unlink requires another being promoted first. |
| SSRF in tool args | No tool fetches URLs in v1. If added: allowlist + DNS pinning. |
| Prompt injection in stored memory | Memory is per-tenant; injection cannot leak across tenants. Within tenant, memories belong to the tenant. |
| Embedding inversion | Embeddings stored with content; column-level encryption deferred to v2. |
| Bedrock data retention | Bedrock does not retain inputs (per AWS policy); embeddings only. |
| Bedrock cross-region inference | Avoided in v1; only Titan v2 in `ap-south-1`. |
| EBS snapshot leak | KMS-encrypted; IAM-restricted access. |
| Backup bucket leak | Block Public Access; bucket policy denies non-TLS; SSE-KMS; GPG layer. |
| Operator key compromise | Rotate quarterly; audit `auth.log`; bastion deferred (single host v1). |
| Supply-chain (pip, npm) | Pinned dependencies with hashes; Renovate/Dependabot alerts; minimal deps. |
| Privilege escalation via mem_app role | mem_app has no DDL, no DELETE on audit_log, no superuser. mem_maint runs only as cron. |
| RCE via Caddy/uvicorn/Postgres CVE | unattended-upgrades; alerts on critical CVEs in deps. |
| Bedrock cost runaway | Per-tenant daily cap + global circuit breaker. |
| Cognito quota DoS | Shim daily cap on CreateUserPoolClient + global alarm. |
| DNS hijack | DNSSEC on Route 53 zone (operator setup). |

---
## 16. Repository Layout & Coding Standards

### 16.1 Repository structure

```
mem-mcp/
├── README.md
├── LICENSE
├── docs/
│   ├── architecture.md           # Pointer to this spec
│   ├── runbooks/
│   │   ├── add_beta_user.md
│   │   ├── suspend_tenant.md
│   │   ├── dpdp_export_request.md
│   │   ├── dpdp_delete_request.md
│   │   ├── restore_from_backup.md
│   │   ├── rotate_jwt_keys.md
│   │   ├── investigate_token_reuse.md
│   │   ├── add_allowed_software.md
│   │   ├── lockout_recovery.md
│   │   └── tenant_merge.md
│   └── adr/                       # Architecture Decision Records
│       ├── 0001-cognito-with-dcr-shim.md
│       ├── 0002-single-vm-postgres.md
│       ├── 0003-titan-embed-v2-not-cris.md
│       ├── 0004-tenant-identities-table.md
│       └── 0005-versioning-by-type.md
├── infra/
│   ├── terraform/
│   │   ├── versions.tf
│   │   ├── providers.tf
│   │   ├── variables.tf
│   │   ├── main.tf                # composition of modules
│   │   ├── outputs.tf
│   │   └── modules/
│   │       ├── network/
│   │       ├── compute/
│   │       ├── identity/          # Cognito user pool, IdPs, resource server
│   │       ├── secrets/           # KMS, SSM
│   │       ├── storage/           # S3 backups
│   │       ├── lambda_presignup/
│   │       ├── observability/
│   │       └── dns/
│   └── cloud-init/
│       └── user-data.yaml
├── deploy/
│   ├── Caddyfile
│   ├── systemd/
│   │   ├── mem-mcp.service
│   │   ├── mem-web.service
│   │   ├── mem-mcp-retention-memories.timer
│   │   ├── mem-mcp-retention-memories.service
│   │   ├── mem-mcp-retention-tokens.timer
│   │   ├── mem-mcp-retention-tokens.service
│   │   ├── mem-mcp-retention-audit.timer
│   │   ├── mem-mcp-retention-audit.service
│   │   ├── mem-mcp-retention-deletion.timer
│   │   ├── mem-mcp-retention-deletion.service
│   │   ├── mem-mcp-cleanup-clients.timer
│   │   ├── mem-mcp-cleanup-clients.service
│   │   ├── mem-mcp-backup.timer
│   │   └── mem-mcp-backup.service
│   ├── postgres/
│   │   ├── postgresql.conf.fragment
│   │   └── pg_hba.conf
│   └── scripts/
│       ├── bootstrap.sh
│       ├── deploy.sh
│       ├── pg_dump_to_s3.sh
│       ├── restore_from_s3.sh
│       └── seed_invite.py
├── alembic/
│   ├── env.py
│   └── versions/
│       ├── 0001_initial_schema.py
│       └── 0002_seed_allowed_software.py
├── alembic.ini
├── pyproject.toml
├── poetry.lock
├── .python-version
├── .pre-commit-config.yaml
├── lambdas/
│   └── presignup/
│       ├── handler.py
│       ├── requirements.txt
│       └── tests/
├── src/
│   └── mem_mcp/
│       ├── __init__.py
│       ├── main.py                # FastAPI app entrypoint (mem-mcp)
│       ├── web_main.py            # FastAPI app entrypoint for the /api/web routes — same process or split? See §16.3
│       ├── config.py              # pydantic settings, SSM loader
│       ├── logging_setup.py       # structlog config
│       ├── db/
│       │   ├── __init__.py
│       │   ├── pool.py            # asyncpg pool factory
│       │   ├── tenant_tx.py       # the canonical tenant context manager
│       │   └── helpers.py
│       ├── auth/
│       │   ├── __init__.py
│       │   ├── jwt_validator.py   # Cognito JWKS validation
│       │   ├── middleware.py      # Bearer middleware → request.state.tenant
│       │   ├── well_known.py      # PRM + AS metadata endpoints
│       │   ├── dcr.py             # /oauth/register handler
│       │   ├── dcr_admin.py       # /oauth/register/{client_id} GET/DELETE
│       │   ├── consent.py         # custom consent screen
│       │   └── internal_invite.py # /internal/check_invite (Lambda peer)
│       ├── identity/
│       │   ├── linking.py
│       │   ├── unlinking.py
│       │   └── lifecycle.py       # closure, recovery
│       ├── mcp/
│       │   ├── transport.py       # Streamable HTTP handler, JSON-RPC dispatch
│       │   ├── registry.py        # tool registration
│       │   ├── errors.py
│       │   └── tools/
│       │       ├── write.py
│       │       ├── search.py
│       │       ├── get.py
│       │       ├── list.py
│       │       ├── update.py
│       │       ├── delete.py
│       │       ├── undelete.py
│       │       ├── supersede.py
│       │       ├── export.py
│       │       ├── stats.py
│       │       └── feedback.py
│       ├── memory/
│       │   ├── normalize.py
│       │   ├── dedupe.py
│       │   ├── hybrid_query.py    # the canonical SQL + scoring
│       │   └── versioning.py
│       ├── embeddings/
│       │   └── bedrock.py         # Titan v2 client (boto3 with Tenacity)
│       ├── quotas/
│       │   ├── tiers.py
│       │   ├── enforcer.py
│       │   └── usage.py           # tenant_daily_usage helpers
│       ├── ratelimit/
│       │   ├── token_bucket.py
│       │   └── dcr_limits.py
│       ├── audit/
│       │   └── logger.py
│       ├── ses/
│       │   └── mailer.py
│       ├── web/
│       │   ├── routes.py          # /api/web/*
│       │   ├── sessions.py        # cookie sessions
│       │   ├── csrf.py
│       │   └── handlers/          # one per page or domain
│       └── jobs/
│           ├── retention_memories.py
│           ├── retention_tokens.py
│           ├── retention_audit.py
│           ├── retention_deletion.py
│           ├── cleanup_clients.py
│           └── backup_check.py
├── web/                             # Next.js frontend
│   ├── package.json
│   ├── tsconfig.json
│   ├── next.config.mjs
│   ├── tailwind.config.ts
│   ├── postcss.config.js
│   ├── public/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx                  # /
│   │   ├── welcome/page.tsx
│   │   ├── dashboard/page.tsx
│   │   ├── memories/page.tsx
│   │   ├── memories/[id]/page.tsx
│   │   ├── settings/page.tsx
│   │   ├── settings/identities/page.tsx
│   │   ├── settings/applications/page.tsx
│   │   ├── settings/feedback/page.tsx
│   │   ├── data/export/page.tsx
│   │   ├── data/delete/page.tsx
│   │   ├── skills/page.tsx
│   │   └── legal/
│   │       ├── privacy/page.tsx
│   │       └── terms/page.tsx
│   ├── lib/
│   │   ├── api.ts
│   │   └── auth.ts
│   └── components/
├── skills/
│   ├── mem-capture/
│   │   ├── SKILL.md
│   │   └── meta.yaml
│   └── mem-recall/
│       ├── SKILL.md
│       └── meta.yaml
├── tests/
│   ├── unit/
│   ├── integration/
│   └── security/
└── eval/
    ├── retrieval/
    │   ├── dataset.jsonl
    │   └── run_eval.py
    └── README.md
```

### 16.2 Coding standards

#### 16.2.1 Python

- **Python 3.12**, type hints everywhere, `from __future__ import annotations`.
- **Formatter**: ruff format. **Linter**: ruff (replaces flake8/isort/black). `ruff check` clean on every commit.
- **Type checking**: mypy strict on `src/mem_mcp`.
- **Async**: FastAPI + asyncpg; no synchronous DB calls in request handlers.
- **Imports**: absolute only.
- **Error handling**: never bare `except:`. Always log with structured fields. Raise typed exceptions defined in `mem_mcp/errors.py`.
- **Logging**: structlog with bound context; no f-string interpolation into log messages — pass kwargs.
- **DB access pattern**: every tenant-scoped function takes `conn: Connection` parameter from a `tenant_tx` block; never opens its own pool acquisition.
- **No print()** anywhere in source.
- **Configuration**: pydantic `BaseSettings`; all values come from env/SSM at startup; no env reads scattered through code.

#### 16.2.2 TypeScript / Next.js

- **TypeScript strict** (`"strict": true`).
- **ESLint** + **Prettier**.
- **No `any`** without an explanatory comment.
- **Server Components** by default; Client Components only where interactivity demands.
- **Server Actions** for mutations rather than REST POST where it simplifies.
- **No client-side secrets** (NEXT_PUBLIC_* contains only public values).

#### 16.2.3 SQL

- All schema changes via Alembic migrations.
- Query parameter binding ALWAYS via `$1` style (asyncpg) or named (`:foo`); never string concatenation.
- New tenant-scoped tables MUST add RLS policies in the same migration.
- Indexes added with `CONCURRENTLY` post-launch only (initial migration runs before traffic).

### 16.3 Process layout

- **Single-process v1**: `mem-mcp` FastAPI app handles `/mcp`, `/oauth/*`, `/.well-known/*`, `/api/web/*`, `/auth/*`, `/internal/*`. Web Next.js renders pages and proxies API calls to itself.
- **Why single process**: simpler deployment, shared pool, shared audit logger. Routes are well-segmented by path prefix.
- **Future split (v2 if scale demands)**: `mem-mcp` keeps `/mcp`, separate `mem-web-api` takes `/api/web/*`. Done by extracting routers, no schema changes.

### 16.4 Pre-commit

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.x
    hooks:
      - id: ruff
      - id: ruff-format
  - repo: local
    hooks:
      - id: tenant-id-linter
        name: Ensure tenant-scoped queries include WHERE tenant_id
        entry: python tools/lint_tenant_scope.py
        language: system
        pass_filenames: true
        files: ^src/mem_mcp/.*\.py$
      - id: mypy
        name: mypy
        entry: poetry run mypy src/mem_mcp
        language: system
        pass_filenames: false
```

### 16.5 CI

GitHub Actions:

- **lint**: ruff check + ruff format check + mypy.
- **unit**: pytest unit suite.
- **integration**: pytest integration suite against an ephemeral Postgres + LocalStack-mocked Bedrock.
- **security**: pytest security suite (§18.3) — REQUIRED check, blocks merge.
- **web-build**: `pnpm build` of the Next.js app.
- **terraform-validate**: `terraform fmt -check` + `terraform validate`.

---

## 17. Phase Plan & Full Task List

The task list below is structured for direct conversion to GitHub issues. Each task has an ID (`T-x.y.z`) for cross-referencing in PRs and other tasks. Task IDs are stable; if a task is dropped, retire the ID rather than reuse.

> **Convention for issues**: title = `[T-x.y.z] short summary`. Body should reference this section, list the FRs/NFRs the task implements, and copy the acceptance bullets. Labels: `phase-N`, `area-{auth,mcp,memory,web,infra,tests,docs,ops}`, `priority-{p0,p1,p2}`.

### Phase 0 — Bootstrap & external prerequisites

**Goal**: AWS account, domain, third-party app credentials ready.

- **T-0.1 Domain & Route 53 hosted zone** — Register/select domain; create hosted zone in AWS; configure NS at registrar. AC: `dig <domain> NS` resolves to AWS. `area-infra`, `priority-p0`.
- **T-0.2 AWS account hardening** — Enable CloudTrail in `ap-south-1`; enable AWS Config; create operator IAM user with MFA; root account locked. AC: CloudTrail events visible. `area-infra`, `priority-p0`.
- **T-0.3 Bedrock model access** — Enable `amazon.titan-embed-text-v2:0` in `ap-south-1` console. AC: `aws bedrock-runtime invoke-model` returns vector. `area-infra`, `priority-p0`.
- **T-0.4 SES verified domain** — Verify domain in Mumbai; configure DKIM; create configuration set. AC: domain status = verified. `area-ops`, `priority-p0`.
- **T-0.5 SES sandbox-removal request** — File support ticket. AC: ticket open. (Resolution can take days; start now.) `area-ops`, `priority-p0`.
- **T-0.6 Google OAuth client** — Create Google Cloud project; configure consent screen; create OAuth 2.0 client; add `https://auth.<domain>/oauth2/idpresponse` redirect URI; record client_id + secret in SSM. AC: client created. `area-auth`, `priority-p0`.
- **T-0.7 GitHub OAuth app** — Create GitHub OAuth app; record client_id + secret. AC: app created. `area-auth`, `priority-p0`.
- **T-0.8 Operator KMS key** — Create CMK `alias/mem-mcp` with rotation enabled. AC: key reachable via CLI. `area-infra`, `priority-p0`.

### Phase 1 — Infrastructure (Terraform)

**Goal**: VM + DNS + Cognito + supporting AWS resources stood up reproducibly.

- **T-1.1 Terraform skeleton** — `versions.tf`, `providers.tf` (S3 backend with DynamoDB lock), `variables.tf`. AC: `terraform init` succeeds.
- **T-1.2 Network module** — VPC, subnet, IGW, route table, SG. AC: `terraform plan` clean.
- **T-1.3 Compute module** — EC2 t4g.medium, EBS gp3, Elastic IP, instance role with all permissions per §4.9. AC: instance launches, EIP attaches.
- **T-1.4 Storage module** — S3 backup bucket with policy + lifecycle. AC: bucket exists, public access blocked.
- **T-1.5 Identity module** — Cognito user pool, custom domain, resource server with custom scopes, Google + GitHub IdPs from SSM secrets, web app client (confidential). AC: Cognito Hosted UI loads at `auth.<domain>`.
- **T-1.6 Secrets module** — SSM parameters listed in §4.8 created (with placeholder values).
- **T-1.7 Lambda module** — `mem-mcp-presignup` Lambda packaged from `lambdas/presignup/`; Cognito trigger wired. AC: Lambda invokable; trigger configured.
- **T-1.8 Observability module** — CloudWatch log groups, alarms (initial set), dashboard. AC: alarms in OK state.
- **T-1.9 DNS module** — A records for `mem.<domain>`, `app.<domain>`; ALIAS for `auth.<domain>`. AC: DNS resolves.
- **T-1.10 ACM cert (us-east-1)** — Required for Cognito custom domain. AC: cert validated.
- **T-1.11 Terraform output check** — `terraform output` produces all values needed by cloud-init.
- **T-1.12 cloud-init user-data** — Bootstraps Caddy, Postgres 16 + extensions, Python 3.12, Node 20, awscli, CloudWatch agent, fail2ban, unattended-upgrades. Pulls repo. Calls `bootstrap.sh`. AC: VM reaches `/healthz` (stubbed) → 200.

### Phase 2 — Database & migrations

- **T-2.1 Alembic init** — Project skeleton. AC: `alembic upgrade head` no-ops successfully.
- **T-2.2 Migration 0001 — initial schema** — Encode §8.3 DDL. AC: all tables and indexes exist.
- **T-2.3 Migration 0002 — seed allowed_software** — Per §8.4. AC: rows present.
- **T-2.4 Roles & grants script** — `deploy/postgres/init_roles.sql` creates `mem_app` and `mem_maint`, applies grants. AC: roles exist, `\dp memories` shows correct grants.
- **T-2.5 RLS smoke test (manual)** — Connect as `mem_app` without `SET LOCAL`; verify `SELECT * FROM memories` returns 0 rows. AC: documented in test runbook.

### Phase 3 — App skeleton & config

- **T-3.1 Poetry project** — `pyproject.toml`, deps (FastAPI, asyncpg, structlog, boto3, pydantic-settings, tenacity, python-jose). AC: `poetry install` clean.
- **T-3.2 Config module** — Pydantic Settings with SSM loader. AC: `mem_mcp.config.get_settings()` loads from SSM at runtime.
- **T-3.3 Logging setup** — structlog JSON to stdout; redact filter for known sensitive keys. AC: log emitted in JSON.
- **T-3.4 Database pool & tenant_tx** — `mem_mcp.db.pool` and `mem_mcp.db.tenant_tx`. AC: unit test for `tenant_tx` SET LOCAL discipline.
- **T-3.5 FastAPI app entry** — `main.py` with `/healthz` and `/readyz`. `/readyz` checks DB + Bedrock + Cognito JWKS reachability. AC: both endpoints return 200 in healthy state.
- **T-3.6 systemd unit** — `mem-mcp.service`. AC: `systemctl restart mem-mcp` works.
- **T-3.7 Caddyfile** — TLS for `mem.<domain>` and `app.<domain>`; reverse proxy. AC: `curl https://mem.<domain>/healthz` returns 200 with valid cert.

### Phase 4 — Authentication & DCR shim

- **T-4.1 Cognito JWKS validator** — Fetch + cache JWKS; verify JWT signature, iss, aud, exp. Unit-tested with synthetic JWTs. AC: tests pass.
- **T-4.2 Bearer middleware** — Extract token, validate, lookup `tenant_identities`, set `request.state.tenant`. Reject suspended/deleted. AC: integration test exercises 401, 403, success.
- **T-4.3 Well-known endpoints** — `/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server`. AC: JSON shapes match §6.3 / §6.4.
- **T-4.4 DCR endpoint POST /oauth/register** — Validate per §6.5; allowlist check; per-IP rate limit; create Cognito client; persist row; return RFC 7591 response. AC: end-to-end test with Cognito (LocalStack-style mock for unit; live for integration).
- **T-4.5 DCR admin endpoints** — GET/DELETE `/oauth/register/{client_id}` with registration_access_token auth. AC: tests.
- **T-4.6 Custom consent screen** — Authorize wrapper page that displays client identity + scopes + verified badge before redirecting to Cognito Hosted UI. Records into `oauth_consents`. AC: visual smoke test + persistence test.
- **T-4.7 Internal invite check endpoint** — `/internal/check_invite` shared-secret-protected. AC: returns correct decisions per §7.3.3.
- **T-4.8 PreSignUp Lambda** — `lambdas/presignup/handler.py` calls `/internal/check_invite`; allows/denies per response; emits structured log. AC: integration test against live Cognito flow.
- **T-4.9 DCR cleanup job** — Daily systemd timer per FR-6.5.10. AC: unit test of decision logic.
- **T-4.10 OAuth integration test** — Full DCR + authorize + token + MCP call cycle. AC: passes against live Cognito in a staging deployment.

### Phase 5 — MCP transport & first tools

- **T-5.1 Streamable HTTP handler** — `/mcp` accepts JSON-RPC; validates Bearer; routes to tool registry. AC: minimal `tools/list` works.
- **T-5.2 401 + WWW-Authenticate path** — Unauthenticated request returns proper headers per §9.1.5. AC: tested.
- **T-5.3 Tool registry & dispatch** — `mem_mcp.mcp.registry`; per-tool scope check. AC: dispatch tested.
- **T-5.4 Bedrock embedding client** — `mem_mcp.embeddings.bedrock`; Tenacity retries; token-count capture. AC: integration test against live Bedrock.
- **T-5.5 Tool: memory.write** — Per §9.3.1; including dedupe (§10.5). AC: tests.
- **T-5.6 Tool: memory.search** — Per §9.3.2; uses §10.3 SQL with per-type recency_lambda. AC: tests with curated dataset showing expected ordering.
- **T-5.7 Tool: memory.get** — Per §9.3.3. AC: tests.
- **T-5.8 Audit logger** — `mem_mcp.audit.logger`; called from each tool. AC: per-tool tests verify a row is written.
- **T-5.9 First Claude Code end-to-end** — Manual: `claude mcp add` against staging; complete OAuth; write & retrieve a memory. AC: documented success.

### Phase 6 — Tenant isolation hardening (gating phase)

> No external user is invited until this phase passes.

- **T-6.1 Tenant-scope linter** — `tools/lint_tenant_scope.py` flags SQL touching `memories` without `tenant_id`. CI gate. AC: catches a planted regression.
- **T-6.2 Two-tenant fixtures** — pytest fixture creating tenants A and B. AC: fixture available.
- **T-6.3 Test: cross-tenant search isolation** — Per §18.3.1. AC: passes.
- **T-6.4 Test: SQLi probes in tags/query** — Per §18.3.2. AC: passes.
- **T-6.5 Test: RLS fail-closed without SET LOCAL** — Per §18.3.3. AC: passes.
- **T-6.6 Test: pool-leak under concurrency** — Per §18.3.4. AC: passes.
- **T-6.7 Test: scope enforcement** — `memory.read` token cannot call `memory.write`. AC: passes.
- **T-6.8 Test: token reuse / rotation** — Cognito's revocation behavior verified end-to-end. AC: passes.
- **T-6.9 Test: tenant status enforcement** — Suspended tenant cannot call `/mcp`. AC: passes.
- **T-6.10 Synthetic alarm test** — Nightly job in CI that runs the security suite against staging; on fail, alarm fires. AC: alarm fires when test fails.
- **T-6.11 Manual review of test code** — Documented sign-off (operator) that the tests themselves are correct. AC: signed PR comment.

### Phase 7 — Remaining tools, identities, lifecycle

- **T-7.1 Tool: memory.list** — §9.3.4 with cursor pagination. AC: tests.
- **T-7.2 Tool: memory.update** — §9.3.5 with versioning rules. AC: tests covering both versioned and in-place paths.
- **T-7.3 Tool: memory.delete** — §9.3.6. AC: tests.
- **T-7.4 Tool: memory.undelete** — §9.3.7. AC: tests including grace-period boundary.
- **T-7.5 Tool: memory.supersede** — §9.3.8. AC: tests.
- **T-7.6 Tool: memory.export** — §9.3.9. AC: tests.
- **T-7.7 Tool: memory.stats** — §9.3.10. AC: tests.
- **T-7.8 Tool: memory.feedback** — §9.3.11. AC: tests.
- **T-7.9 Quota enforcer** — `mem_mcp.quotas.enforcer` checks memories_count + daily tokens + per-minute writes/reads; integrated into write/search. AC: unit + integration tests.
- **T-7.10 Identity linking endpoints** — `/api/web/identities/link/start` and complete; signed link_state. AC: tests.
- **T-7.11 Identity unlink + promote** — Per §7.5/7.6. AC: tests.
- **T-7.12 Account closure flow** — Per §7.7. AC: tests covering cancel and finalize paths.
- **T-7.13 Connected applications revoke** — `/api/web/clients/{id}` DELETE. AC: tests.
- **T-7.14 Retention jobs** — Memories, tokens, audit, deletion, cleanup. Each as systemd timer. AC: dry-run mode tests + real run on staging.

### Phase 8 — Web application

- **T-8.1 Next.js scaffold** — App Router, Tailwind, shadcn-style components. AC: builds, runs.
- **T-8.2 Cognito login + callback** — `/auth/login`, `/auth/callback`. Sessions in `web_sessions`. AC: end-to-end login.
- **T-8.3 Onboarding `/welcome`** — Per §12.3.2 with copy-paste cards. AC: visual review.
- **T-8.4 Dashboard `/dashboard`** — Stats + quota bars. AC: numbers match `memory.stats`.
- **T-8.5 Memories list `/memories`** — Filters, search, pagination. AC: visual + functional test.
- **T-8.6 Memory detail `/memories/{id}`** — Display, edit, delete, undelete, version history, audit trail. AC: visual + functional.
- **T-8.7 Settings `/settings`** — Profile + retention + close account. AC: tests.
- **T-8.8 Identities page** — Per §12.3.7. AC: link/unlink flows tested.
- **T-8.9 Applications page** — Per §12.3.8. AC: revoke verified.
- **T-8.10 Feedback page** — Per §12.3.9. AC: persists.
- **T-8.11 Data export page** — Streams JSON. AC: large export tested.
- **T-8.12 Account closure UI** — Per §12.3.11. AC: cancel and finalize tested.
- **T-8.13 Skills page `/skills`** — Static instructions + downloads. AC: visual.
- **T-8.14 Legal pages** — Privacy, Terms (drafts to be reviewed by counsel before Google OAuth verification). AC: pages render.
- **T-8.15 CSRF + CSP** — Per §12.6. AC: tests.

### Phase 9 — Skills, integration, and beta-readiness polish

- **T-9.1 mem-capture skill** — Per §13.2 + a meta.yaml describing connector wiring. AC: works in Claude Code against staging.
- **T-9.2 mem-recall skill** — Per §13.3. AC: works in Claude Code.
- **T-9.3 Claude.ai project instructions block** — Per §13.4; tested with 3 invitees. AC: 3 successful captures + recalls reported.
- **T-9.4 ChatGPT custom GPT instructions** — Per §13.5; tested. AC: same.
- **T-9.5 Eval harness** — `eval/retrieval/` with 20 query/expected pairs; baseline metrics recorded. AC: harness runnable; baseline captured.
- **T-9.6 CloudWatch alarms full** — Wire all alarms from §14.4 to SNS topic; subscribe operator email. AC: synthetic failure triggers alarm.
- **T-9.7 Backup & restore drill** — Run nightly backup; restore to a fresh VM; verify integrity. AC: documented success.
- **T-9.8 Operator runbooks** — Author all runbooks per §14.6; each one exercised once. AC: PR with each marked complete.
- **T-9.9 Load test** — 100 concurrent searches; record p50/p95/p99. AC: p95 < 250ms (excluding Bedrock ~50ms).
- **T-9.10 README and onboarding doc for invitees** — A small `INVITE.md` explaining first steps. AC: review.

### Phase 10 — Closed beta launch

- **T-10.1 Seed first invitee (operator self)** — Insert into `invited_emails`; complete sign-in; connect Claude Code; write & recall. AC: success.
- **T-10.2 Seed 2-3 invitees** — Send invites; observe issues. AC: 3/3 onboard successfully.
- **T-10.3 Triage feedback for one week** — Address top issues. AC: feedback queue empty.
- **T-10.4 Expand beta** — Up to ~10 invitees. AC: capacity holding.

### Phase 11 (post-beta) — Hardening & v2 prep

(Out of scope for this plan but listed so issues can be created with `phase-11` label.)

- T-11.1 Reranker integration (Bedrock Cohere Rerank).
- T-11.2 Agentic DCR review pipeline.
- T-11.3 Stripe billing for tier upgrades.
- T-11.4 Additional IdPs (Apple, Microsoft).
- T-11.5 Multi-region failover.
- T-11.6 Public signup with CAPTCHA.
- T-11.7 Async backfill ingestion.
- T-11.8 Column-level encryption of `content`.
- T-11.9 Tenant merge UI.
- T-11.10 LLM-based recall preflight classifier (when CRIS data-flow position is acceptable).

---
## 18. Testing Strategy

### 18.1 Unit tests (`tests/unit/`)

Fast, no DB or network. Deterministic.

- **U-1** JWT validation (synthetic JWTs with known JWKS).
- **U-2** Content normalization for hashing.
- **U-3** Hybrid scoring math (given fake sem/kw scores + ages → expected ranking).
- **U-4** Quota enforcement decisions (state-driven, not DB-bound).
- **U-5** Tag/project sanitization, redirect_uri validation, software_id allowlist matching.
- **U-6** MCP JSON-RPC error mapping.
- **U-7** Tier resolution (override vs default).
- **U-8** Recency_lambda by type lookup.

### 18.2 Integration tests (`tests/integration/`)

Use ephemeral Postgres (Docker) and a Bedrock stub (custom FastAPI app returning canned embeddings) by default; opt-in `--live-aws` flag to run against real Cognito/Bedrock in a staging account.

- **I-1** OAuth: discovery → DCR → authorize → token → MCP call (full happy path).
- **I-2** Tool: write happy path; row inserted; embedding stored; usage row updated.
- **I-3** Tool: search returns expected ordering on a curated 50-memory dataset.
- **I-4** Tool: update — versioned vs in-place behavior per type.
- **I-5** Tool: delete + undelete + grace-period boundary.
- **I-6** Tool: supersede chains correctly.
- **I-7** Tool: export returns full tenant data.
- **I-8** Tool: stats returns correct counts.
- **I-9** Tool: feedback persists.
- **I-10** Quota: write quota exceeded returns structured error.
- **I-11** Quota: daily token cap stops further writes.
- **I-12** Identity: link flow with signed state.
- **I-13** Identity: unlink last-identity refused.
- **I-14** Account closure: cancel within window returns to active.
- **I-15** Account closure: finalization deletes data.
- **I-16** Retention: soft-delete and hard-delete jobs process correct rows.
- **I-17** Web session lifecycle: login, idle, logout.
- **I-18** Web API: each endpoint reachable with valid session, rejected without.
- **I-19** PreSignUp Lambda decisions (allow / deny / collision).
- **I-20** DCR cleanup deletes unused clients.

### 18.3 Security tests (`tests/security/`) — REQUIRED CI gate

- **S-1 Cross-tenant search isolation**:

```python
@pytest.mark.security
async def test_cross_tenant_search_isolation(setup_two_tenants, client):
    a, b = setup_two_tenants
    await client.write(a.token, "secret atlas pivot decision")
    res = await client.search(b.token, "atlas pivot")
    assert res == []
```

- **S-2 SQL injection in tags/query/type**:

```python
@pytest.mark.security
@pytest.mark.parametrize("payload", [
    "'; DROP TABLE memories;--",
    "' OR tenant_id != tenant_id --",
    "{1,2}",
    "x' UNION SELECT * FROM tenants --",
])
async def test_injection_in_inputs(setup_two_tenants, client, payload):
    a, b = setup_two_tenants
    await client.write(b.token, "b's data")
    res = await client.search(a.token, query="data", tags=[payload])
    assert all(r["tenant_id"] == str(a.tenant_id) for r in res)
```

- **S-3 RLS fail-closed**:

```python
@pytest.mark.security
async def test_rls_failclosed(raw_pool):
    async with raw_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM memories LIMIT 100")
    assert rows == []
```

- **S-4 Pool tenancy isolation under concurrency**:

```python
@pytest.mark.security
async def test_pool_does_not_leak_setting(pool):
    async def use(tid):
        async with tenant_tx(pool, tid) as conn:
            cur = await conn.fetchval("SELECT current_setting('app.current_tenant_id', true)")
            assert cur == str(tid)
    await asyncio.gather(*[use(uuid4()) for _ in range(50)])
```

- **S-5 Scope enforcement**: token without `memory.write` rejected on `memory.write`.
- **S-6 Suspended tenant**: 403 with `account_suspended`.
- **S-7 Pending-deletion tenant**: 403 with `account_deletion_pending`.
- **S-8 Email collision**: PreSignUp Lambda denies signup when email already mapped to existing tenant.
- **S-9 Link flow attacks**: tampered link_state HMAC rejected; expired state rejected; cross-session state (different web session) rejected.
- **S-10 DCR allowlist**: unknown software_id → 403; blocked software → 403.
- **S-11 DCR rate limit**: per-IP and global caps trigger 429.
- **S-12 Token tampering**: any single-bit change to JWT signature rejected.
- **S-13 Token expiry**: expired access token rejected.
- **S-14 Client revocation**: revoking via `/api/web/clients/{id}` invalidates further calls within minutes.
- **S-15 Audit completeness**: every successful and denied operation appends an audit row.
- **S-16 Web CSRF**: requests without valid CSRF token rejected.
- **S-17 RLS WITH CHECK**: attempting to INSERT a memory with mismatched tenant_id rejected.
- **S-18 Cross-tenant identity link**: linking a cognito_sub already linked to another tenant returns 409.

### 18.4 Property-based (Hypothesis)

- **P-1** Random `(content, tags, type)` writes followed by exact-keyword searches always include the written memory.
- **P-2** Soft-deleted memories never appear in default search.
- **P-3** Returned `tenant_id` always equals caller's tenant_id (universal invariant).
- **P-4** Hashing is deterministic under whitespace/case variations.
- **P-5** Updating non-versioned types preserves `id`; updating versioned types creates new id with `is_current=true`.

### 18.5 Retrieval evaluation harness (`eval/retrieval/`)

- **E-1** Dataset: 20 hand-curated `(state, query, expected_top_3_ids)` triples covering: exact recall, paraphrase, recency-relevant, decision supersedence, multi-tag filter.
- **E-2** Runner: loads dataset, prepares state, executes search, computes top-3 hit-rate and MRR. Outputs JSON report.
- **E-3** Baseline numbers recorded after Phase 5; regressions caught by re-running.
- **E-4** Eval harness used to tune `recency_lambda` and `w_sem`/`w_kw` weights during Phase 7-9.

### 18.6 Manual / exploratory

- **M-1** Real Claude Code session (~30 min) of spontaneous capture and recall.
- **M-2** Same in Claude.ai project.
- **M-3** Same in ChatGPT custom GPT.
- **M-4** Each platform's expected behavior documented; drift over time tracked.

---

## 19. Open Questions / Future Work

These are deferred from v1; record decisions here as they arise.

1. **Reranker integration.** Cohere Rerank via Bedrock Mumbai's availability; fallback to a small local reranker if not.
2. **Agentic DCR review.** Built atop the `pending_review` state in `allowed_software` and `oauth_clients.review_status`. Inputs: full DCR payload, IP, software_statement. Outputs: agent decision + structured rationale into `review_payload`. Human review queue for `needs_human_review`.
3. **Reputation/verified-badge service.** Decoupled from this codebase; could reference a community-curated registry of MCP clients.
4. **Stripe billing.** Tier transitions trigger limit changes immediately; downgrades grace-period prevents data loss.
5. **More IdPs.** Apple, Microsoft. Some require ASIWebAuthenticationSession native shells if mobile is targeted.
6. **Multi-region failover.** Streaming replication to `ap-south-2`; DNS failover via Route 53 health checks.
7. **Async backfill ingestion.** Pull Claude Code transcripts via Code's export API (when available) → extract memorable items via local LLM → insert.
8. **Column-level encryption.** Use libsodium per-tenant key derived from a master KMS key; keep tsv on plaintext-derived hashed tokens (loses partial recall) OR drop FTS for encrypted users.
9. **Tenant merge UI.** Risk-managed admin tool to combine `T2` into `T1` without data corruption.
10. **LLM recall classifier.** Replaces the heuristic preflight in skills with a Bedrock call (CRIS-flagged) for higher precision.
11. **MFA.** Cognito advanced security mode ENFORCED + MFA option; per-tenant policy.
12. **Skill marketplace.** Versioned skills, automatic updates.
13. **Bulk import.** CSV / JSONL upload via web UI for migration from competitor services.
14. **Webhook delivery.** Tenant subscribes to memory write events for downstream workflows.
15. **API tokens (vs OAuth).** Long-lived tokens for scripted access; needs careful scope and revocation UX.

---

## 20. Appendices

### Appendix A — Reference RFCs / specs

- RFC 6749 (OAuth 2.0)
- RFC 6750 (Bearer)
- RFC 7009 (Token revocation)
- RFC 7591 (Dynamic Client Registration)
- RFC 7636 (PKCE)
- RFC 8414 (Authorization server metadata)
- RFC 8707 (Resource indicators)
- RFC 9728 (Protected Resource Metadata)
- OAuth 2.1 (current draft consolidating best practices)
- MCP specification 2025-06-18 (Streamable HTTP transport)
- DPDP Act 2023 (India)

### Appendix B — Quick reference: client setup

| Client | Connect via |
|---|---|
| Claude Code | `claude mcp add --transport http mem-mcp https://mem.<your-domain>/mcp` then `/mcp` to authenticate |
| Claude.ai (Pro/Max) | Settings → Connectors → Add custom connector → URL `https://mem.<your-domain>/mcp` |
| ChatGPT | Developer Mode → Add MCP connector → URL `https://mem.<your-domain>/mcp` |

### Appendix C — Environment variables (loaded from SSM)

| Var | Source | Purpose |
|---|---|---|
| `MEM_MCP_DB_DSN` | derived | `postgresql+asyncpg://mem_app:...@/mem_mcp` over Unix socket |
| `MEM_MCP_DB_MAINT_DSN` | derived | maintenance role DSN |
| `MEM_MCP_REGION` | const `ap-south-1` | AWS region |
| `MEM_MCP_COGNITO_USER_POOL_ID` | SSM | Cognito user pool id |
| `MEM_MCP_COGNITO_DOMAIN` | const `auth.<your-domain>` | Hosted UI domain |
| `MEM_MCP_RESOURCE_URL` | const `https://mem.<your-domain>` | Resource server identifier |
| `MEM_MCP_WEB_URL` | const `https://app.<your-domain>` | Web app URL |
| `MEM_MCP_WEB_CLIENT_ID` | SSM | Cognito web app client id |
| `MEM_MCP_WEB_CLIENT_SECRET` | SSM | Cognito web app secret |
| `MEM_MCP_INTERNAL_LAMBDA_SECRET` | SSM | shared with PreSignUp Lambda |
| `MEM_MCP_SES_FROM` | SSM | sender address |
| `MEM_MCP_BACKUP_BUCKET` | tf output | backup S3 bucket name |
| `MEM_MCP_BACKUP_GPG_PASSPHRASE` | SSM | encrypts backups |
| `MEM_MCP_WEB_SESSION_SECRET` | SSM | HMAC for sessions/CSRF |
| `MEM_MCP_LINK_STATE_SECRET` | SSM | HMAC for link_state |
| `MEM_MCP_LOG_LEVEL` | env, default INFO | logging |
| `MEM_MCP_BEDROCK_MODEL_ID` | const `amazon.titan-embed-text-v2:0` | embedding model |

### Appendix D — Glossary of error codes (JSON-RPC `data.code`)

| Code | When |
|---|---|
| `insufficient_scope` | Token lacks required scope for the called tool |
| `quota_exceeded` | Per-tenant memories or daily-tokens cap hit |
| `rate_limited` | Per-minute token bucket empty |
| `account_suspended` | Tenant `status='suspended'` |
| `account_deletion_pending` | Tenant in deletion grace period |
| `embedding_unavailable` | Bedrock failed after retries |
| `unauthorized_client` | DCR rejected (allowlist) |
| `dedupe_merged` | (success) write merged into existing memory |
| `cannot_undelete_after_grace_period` | Soft-delete > 30d |
| `cannot_unlink_last_identity` | Refused per FR-7.5.3 |
| `identity_already_linked` | cognito_sub already maps to a tenant |
| `link_state_invalid` | Tampered/expired link_state |
| `email_belongs_to_existing_tenant` | Sign-up blocked by collision |

### Appendix E — Sample Caddyfile

```
{
  email ops@<your-domain>
}

mem.<your-domain> {
  encode zstd gzip
  header {
    Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
    X-Content-Type-Options "nosniff"
    Referrer-Policy "no-referrer"
  }
  reverse_proxy 127.0.0.1:8080
}

app.<your-domain> {
  encode zstd gzip
  header {
    Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
    X-Content-Type-Options "nosniff"
    Referrer-Policy "no-referrer"
    Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; connect-src 'self' https://cognito-idp.ap-south-1.amazonaws.com https://auth.<your-domain>; frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://auth.<your-domain>"
  }
  reverse_proxy 127.0.0.1:8081
}
```

### Appendix F — Sample systemd unit (mem-mcp)

```ini
# /etc/systemd/system/mem-mcp.service
[Unit]
Description=mem-mcp FastAPI service
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=exec
User=memmcp
Group=memmcp
WorkingDirectory=/opt/mem-mcp
EnvironmentFile=/etc/mem-mcp/env
ExecStart=/opt/mem-mcp/.venv/bin/uvicorn mem_mcp.main:app \
    --host 127.0.0.1 --port 8080 \
    --proxy-headers --forwarded-allow-ips '127.0.0.1' \
    --workers 2
Restart=on-failure
RestartSec=5
TimeoutStopSec=15

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/log/mem-mcp /var/lib/mem-mcp
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

### Appendix G — Sample tenant_tx implementation

```python
# src/mem_mcp/db/tenant_tx.py
from __future__ import annotations
import contextlib
from uuid import UUID
import asyncpg

@contextlib.asynccontextmanager
async def tenant_tx(pool: asyncpg.Pool, tenant_id: UUID):
    """
    Acquire a connection, open a transaction, set app.current_tenant_id LOCAL,
    yield. On exit, the SET LOCAL is discarded.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            yield conn
```

### Appendix H — Sample audit row

```json
{
  "id": 17234,
  "tenant_id": "8d2c0a6e-1f3e-4d1a-9d82-9e6e0fadb1f1",
  "actor_client_id": "1a2b3c4d-cognito-client-id",
  "actor_identity_id": "11111111-2222-3333-4444-555555555555",
  "action": "memory.write",
  "target_id": "98765432-...-...-...-...",
  "target_kind": "memory",
  "ip_address": "203.0.113.45",
  "user_agent": "claude-code/2.1.121",
  "request_id": "req_01HXYZ...",
  "result": "success",
  "error_code": null,
  "details": {
    "type": "decision",
    "tags": ["project:ew", "architecture"],
    "deduped": false,
    "embed_tokens": 142,
    "content_length": 837
  },
  "created_at": "2026-04-29T10:14:32.123Z"
}
```

### Appendix I — Definition of Done for v1

- [ ] All Phase 0–9 acceptance criteria met.
- [ ] §18.3 security suite passing in CI; manual review of test code signed off.
- [ ] Operator runbooks executed end-to-end at least once each.
- [ ] First external beta user successfully onboarded (invitation → magic-free sign-in via Google/GitHub → connector → first memory written → recalled from a different client).
- [ ] CloudWatch alarms verified by synthetic failures.
- [ ] Backup/restore drill successful.
- [ ] DPDP export and deletion runbooks executed against a test tenant.
- [ ] Eval harness baseline numbers recorded.
- [ ] CSP / CSRF / TLS scan (testssl.sh or SSL Labs) returns A or A+.
- [ ] No `priority-p0` open issues in the GitHub repo.

---

*End of plan.*
