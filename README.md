# mem-mcp

Personal Memory MCP — a multi-tenant memory service for AI clients (Claude Code, Claude.ai, ChatGPT, ...) over the Model Context Protocol.

**Status**: closed beta. Spec finalized; implementation in progress.

## Documentation

- [`MEMORY_MCP_BUILD_PLAN_V2.md`](./MEMORY_MCP_BUILD_PLAN_V2.md) — canonical HLD/spec
- [`MEMORY_MCP_LLD_V1.md`](./MEMORY_MCP_LLD_V1.md) — low-level design v1 (deltas + module signatures + sequence diagrams + CFT layout + destroy plan)
- [`TASKS_V1.md`](./TASKS_V1.md) — task list (1:1 with GitHub issues)
- [`GUIDELINES.md`](./GUIDELINES.md) — engineering guidelines (XP, AWS-not-simulated, code quality gates, workflow, ops, security)

Open issues are the source of truth for what's next; the markdown task list mirrors them.

## Stack

- AWS `ap-south-1` (Mumbai) — DPDP residency.
- Python 3.12 / FastAPI / asyncpg / PostgreSQL 16 + pgvector.
- Cognito + DCR shim for OAuth 2.1 (Google IdP only in v1).
- Bedrock Titan Embed v2.
- Next.js 15 admin UI.
- CloudFormation + SAM for infra.

See `MEMORY_MCP_LLD_V1.md` §0 for v1 simplifications relative to the spec.

## License

Apache 2.0 — see [`LICENSE`](./LICENSE).
