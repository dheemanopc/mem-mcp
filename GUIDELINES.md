# mem-mcp — Engineering Guidelines

These are the rules of the road. They override conventions, defaults, or anything else that conflicts. When in doubt, follow these. When something is not covered, the spirit of the document — *small, reversible, tested, traceable, cheap* — wins.

---

## 1. Development discipline (XP)

### 1.1 No code without a test
Every PR includes or extends tests. TDD cycle is **red → green → refactor**, in that order, with separate commits where reasonable:

1. `test:` — failing test that captures the desired behavior.
2. `feat:` / `fix:` — minimum code to make the test pass.
3. `refactor:` — cleanup, only on green.

Bug fixes start with a regression test that fails before the fix.

### 1.2 Tests do NOT call real AWS
**Hard rule.** No `moto`, no `localstack`, no live `boto3` in unit or integration tests. Code is structured so AWS calls go through narrow `Protocol` interfaces (e.g. `BedrockClient`, `CognitoAdminClient`, `SsmLoader`, `SesMailer`); production wires the boto3-backed impl, tests inject in-memory fakes.

Tests "assume the AWS resource exists" and verify *our code's behavior*, not AWS's.

**Why:** simulating AWS bloats the bill, slows CI, and creates drift between mock behavior and real behavior. Better to fake at the boundary and verify infra separately via CFT lint + manual live drills.

### 1.3 Live AWS tests are opt-in only
Mark with `@pytest.mark.live_aws`; only run with `pytest --live-aws`. They never gate CI. They exist for end-to-end smoke before deploys.

### 1.4 Real AWS state is owned by CloudFormation
- All infra creation: `sam deploy`.
- All destruction: `deploy/scripts/destroy.sh` (idempotent; LLD §3 — must continue to leave the account at zero ongoing cost).
- Never create AWS resources via Console or `aws ... create-*` in production.

### 1.5 Refactor mercilessly, but only on green
Never refactor while any test is red. Never bundle a refactor with a behavior change in the same commit.

### 1.6 Tests as living documentation
A stranger reading `tests/integration/test_memory_write.py` should learn how `memory.write` behaves without opening `write.py`. Use descriptive test names; one assertion theme per test.

### 1.7 Self-review every PR
Even when working solo: open a branch, push, open a PR, **read your own diff in the GitHub UI** before merging. Catches what tunnel vision in the terminal misses.

### 1.8 Sustainable pace
Closed beta + solo project = no overnight commits, no "I'll fix it Monday" hacks. A 2-day cool-off on a tricky bug is cheaper than a botched 1 AM merge.

---

## 2. Code quality gates (CI must pass before merge)

| Gate | Tool | Required? |
|---|---|---|
| Format | `ruff format --check` | yes |
| Lint | `ruff check` | yes |
| Type check | `mypy --strict src/mem_mcp/` | yes |
| Unit tests | `pytest tests/unit/` | yes |
| Integration tests | `pytest tests/integration/` | yes |
| **Security tests** (tenant isolation) | `pytest tests/security/` | yes — NEVER skip |
| **Tenant-scope linter** | `tools/lint_tenant_scope.py` | yes |
| Secret scan | `gitleaks` (pre-commit + CI) | yes |
| CFT lint | `cfn-lint infra/cfn/**/*.yaml` | yes |
| Web build | `pnpm build` (in `web/`) | yes |

**`# type: ignore` requires a `# reason: …` comment.** No bare ignores.

**Don't merge red CI**, ever. If a test is flaky and not your fault, fix the flake or quarantine it before merging anything else.

---

## 3. Workflow

### 3.1 Trunk-based, small PRs
- Short-lived branches (< 24h preferred).
- PR target: < 400 lines of source diff (tests don't count). Bigger ones split into a stack.
- One issue → one PR (usually). Bigger issues split first.

### 3.2 Conventional commits
Prefixes: `feat:`, `fix:`, `chore:`, `test:`, `docs:`, `refactor:`, `perf:`, `build:`, `ci:`. Cheap to adopt; makes `git log --grep="^fix:"` actually useful and changelogs trivial.

### 3.3 Issue ↔ commit ↔ PR linking
- Every PR title starts with `[T-x.y.z]`.
- Every commit body says `Refs #<issue>` (or `Closes #<issue>` on the merge commit).
- Every issue lists the PR(s) that touch it.

### 3.4 PR template
`.github/pull_request_template.md` includes:
- Linked issue (`Closes #...`)
- Summary (1-3 bullets)
- Test plan (checklist)
- Did you add a runbook entry, if operational?
- Did you update the LLD, if architectural?
- Security review needed? (tenant boundaries, auth, data export)
- Are AC items in the linked issue checked?

### 3.5 Branch protection on `main`
- All required CI checks must pass.
- No force-push.
- No direct push (must go via PR).

---

## 4. Architecture hygiene

### 4.1 Hexagonal seams at every external boundary
Wrap AWS, Postgres, HTTP clients, the clock, and time-zones behind narrow `Protocol`s. Production wires the real impl; tests wire fakes. This makes §1.2 mechanical instead of disciplinary.

### 4.2 ADRs for non-obvious decisions
`docs/adr/NNNN-short-title.md` for any architectural change worth remembering — even small ones. Format: Context · Decision · Consequences · Alternatives considered.

### 4.3 Migrations are forward-only
Never edit a merged Alembic migration. New schema needs a new file. Rollback is "deploy the previous app revision against a strictly-additive newer schema."

### 4.4 Single source of truth
One canonical place per fact:
- `MEMORY_MCP_BUILD_PLAN_V2.md` — original spec (HLD).
- `MEMORY_MCP_LLD_V1.md` — LLD; **wins where it deviates** from the spec for v1.
- GitHub issues — current state of work.
- Code never duplicates a constant from the LLD without a comment back-pointing.

### 4.5 Runbook before feature
If a new feature has any operational surface (alarm, recovery action, manual step), its runbook lands in the **same PR** as the code. No "we'll document later."

---

## 5. Operations & cost

### 5.1 Tag every AWS resource
`Project=mem-mcp` from CFT, plus `Component=<network|compute|identity|...>`. Makes the destroy-script orphan check work, makes Cost Explorer queries accurate, makes IAM resource-tag conditions possible later.

### 5.2 Cost guardrail
- AWS Budget at **$50/mo** with notification at **$40/mo** → SNS to operator.
- AWS Cost Anomaly Detection enabled with email alerts.
- Catches runaway loops before the bill explodes.

### 5.3 Drift detection
Weekly cron runs `aws cloudformation detect-stack-drift` on the root + nested stacks. Any drift = page (someone clicked in the console).

### 5.4 No staging environment in v1
Doubles the cost. Phase 4's "live OAuth integration test" uses a separate Cognito user pool *in the same AWS account*, not a separate stack.

### 5.5 No Datadog / Sentry / etc. in v1
CloudWatch is enough for closed beta. New tools = new ongoing cost + integration debt. Reconsider when scale demands.

### 5.6 Postmortem any incident
Even a small one. A 3-line `docs/postmortems/YYYY-MM-DD-short-name.md` with: what happened · root cause · fix · prevention. Builds institutional memory in a one-person org.

---

## 6. Security

### 6.1 Secrets only in SSM SecureString
Never in `.env` files committed to the repo. Never in CFT parameters. Never in commit messages or PR descriptions. Resources reference SSM by name and resolve at deploy time via `'{{resolve:ssm-secure:...}}'`.

### 6.2 Logs never contain content, secrets, or session ids
Only IDs, lengths, hashes. The redact filter in `mem_mcp/logging_setup.py` is the safety net, not the primary defense.

### 6.3 Tenant isolation is non-negotiable
Three layers (per LLD/spec §5.2): app-level tenant resolver, RLS, explicit `WHERE tenant_id`. Tenant-scope linter enforces. The `tests/security/` suite is a CI gate. **Phase 6 must pass before any external user is invited.**

### 6.4 Pre-commit secret scan
`gitleaks` in `.pre-commit-config.yaml` refuses commits with secret-shaped strings. Bypassing is a deliberate decision recorded in commit message.

---

## 7. What we deliberately don't do

These are tempting but rejected for v1:

- **100% coverage as a target.** Aim for behavior coverage (every public function meaningfully tested), not line coverage. Chasing the last 5% leads to mock-heavy nonsense.
- **Microservices.** Single FastAPI process with route prefixes. Split only when forced.
- **Kubernetes.** Single VM + systemd is enough.
- **Custom auth.** Cognito + thin DCR shim. We do not roll our own OAuth.
- **Multi-region.** Mumbai single-region. Multi-region = v2.
- **Premature LLM in hot path.** No Bedrock LLM calls in v1 — Titan Embed v2 only. Heuristic gates client-side via skills.
- **Bedrock CRIS / cross-region inference.** Avoided to keep DPDP posture clear.

---

## 8. When to break a rule

Every rule above can be broken with a recorded reason. The recipe:

1. State the rule you're breaking and why the standard alternative doesn't work.
2. Get a second pair of eyes (PR review).
3. Add a comment in code (or commit body) so a future reader sees the trade-off.
4. If the break recurs, the rule is wrong — change the rule, not the code.

---

*Living document. PRs welcome.*
