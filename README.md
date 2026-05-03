# mem-mcp

Personal Memory MCP — a multi-tenant memory service for AI clients (Claude Code, Claude.ai, ChatGPT, ...) over the Model Context Protocol.

**Status**: closed beta. Spec finalized; implementation in progress.

## Documentation

- [`MEMORY_MCP_BUILD_PLAN_V2.md`](./MEMORY_MCP_BUILD_PLAN_V2.md) — canonical HLD/spec
- [`MEMORY_MCP_LLD_V1.md`](./MEMORY_MCP_LLD_V1.md) — low-level design v1 (deltas + module signatures + sequence diagrams + CFT layout + destroy plan)
- [`TASKS_V1.md`](./TASKS_V1.md) — task list (1:1 with GitHub issues)
- [`GUIDELINES.md`](./GUIDELINES.md) — engineering guidelines (XP, AWS-not-simulated, code quality gates, workflow, ops, security)
- [`infra/cfn/README.md`](./infra/cfn/README.md) — CloudFormation operator runbook (pre-deploy checklist, deploy order, destroy)

Open issues are the source of truth for what's next; the markdown task list mirrors them.

## Stack

- AWS `ap-south-1` (Mumbai) — DPDP residency.
- Python 3.12 / FastAPI / asyncpg / PostgreSQL 16 + pgvector.
- Cognito + DCR shim for OAuth 2.1 (Google IdP only in v1).
- Bedrock Titan Embed v2.
- Next.js 15 admin UI.
- CloudFormation + SAM for infra.

See `MEMORY_MCP_LLD_V1.md` §0 for v1 simplifications relative to the spec.

## Development quick-start

```bash
# Install
poetry install

# Pre-commit hooks
pre-commit install

# Run all gates locally (mirrors CI)
poetry run ruff check
poetry run ruff format --check
poetry run mypy src/mem_mcp tests
poetry run python tools/lint_tenant_scope.py
poetry run pytest

# Run a single test
poetry run pytest tests/unit/test_<file>.py -v

# Run live AWS / live DB tests
MEM_MCP_TEST_DSN=postgresql://localhost/mem_mcp_test poetry run pytest --live-aws
```

## Engineering guidelines

See [GUIDELINES.md](./GUIDELINES.md). TL;DR:
- TDD: red commit (failing test) → green commit (impl) → refactor.
- All cross-tenant queries go through `tenant_tx` (RLS-scoped); maintenance jobs use `system_tx`. The `tools/lint_tenant_scope.py` linter enforces this.
- Tests use Protocol-shaped fakes — never reach real AWS / live DB without `--live-aws` flag.
- Commits follow conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `style:`, `ops:`, `chore:`).
- PRs land via squash-merge with `Closes #N` (one per line — multi-issue close on one line doesn't work in GitHub).

## Contributing

This is currently a closed-beta project. External PRs not solicited. Existing contributors: see internal handoff notes.

## License

Apache 2.0 — see [`LICENSE`](./LICENSE).
