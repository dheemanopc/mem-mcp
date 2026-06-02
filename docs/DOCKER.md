# Running mem-mcp in containers

This guide covers the containerized deployment of mem-mcp: Postgres + pgvector,
database migrations, the FastAPI API, the maintenance/plugin **jobs** runner,
the Next.js **web** UI, and a **Caddy** reverse proxy — all orchestrated with
Docker Compose. It works two ways:

- **Local dev** (default): config comes entirely from a `.env` file, no AWS
  account required for the service to boot (`MEM_MCP_SKIP_SSM=1`).
- **AWS-backed**: an overlay turns the SSM bypass off so config is read from
  SSM Parameter Store and Cognito/Bedrock/SES work against real AWS.

> The legacy systemd-on-EC2 deployment (`deploy/`) still exists and is
> unchanged. Containers are an alternative packaging of the same code.

## Components

| Service    | Image            | Role |
|------------|------------------|------|
| `postgres` | `pgvector/pgvector:pg16` | Postgres 16 + pgvector; roles + extension created by an init script |
| `migrate`  | `mem-mcp:local`  | One-shot `alembic upgrade head`, then exits |
| `api`      | `mem-mcp:local`  | FastAPI service (uvicorn) on `:8080` |
| `jobs`     | `mem-mcp:local`  | In-process scheduler for core + plugin jobs (replaces systemd timers) |
| `web`      | `mem-mcp-web:local` | Next.js 15 admin UI on `:8081` |
| `caddy`    | `caddy:2`        | Reverse proxy on `:80`, splits API vs web by path |

The app image is **one image, many roles** — the entrypoint dispatches on the
first argument: `api`, `scheduler`, `migrate`, `job <name>`,
`plugin-job <id> <name>`, or any other command (run verbatim, e.g. `bash`).

## Quick start (local)

```bash
cp .env.example .env
# Edit .env: set MEM_APP_PASSWORD / MEM_MAINT_PASSWORD and the matching
# passwords inside MEM_MCP_DB_DSN / MEM_MCP_DB_MAINT_DSN, plus the secrets.

docker compose up --build
```

Then:

- App via Caddy: <http://localhost>
- API directly: <http://localhost:8080> (`/healthz`, `/readyz`, `/mcp`, …)
- Web directly: <http://localhost:8081>
- Postgres: `localhost:5432`

`migrate` runs first and `api`/`jobs` wait for it to finish (and for Postgres
to be healthy) via compose `depends_on` conditions.

> **Note on local auth/embeddings.** The service boots without AWS, but
> Cognito JWT verification, Bedrock embeddings, and SES are real AWS calls.
> Endpoints that need them only work if you supply real AWS credentials
> (see the AWS section). `/healthz` and migrations work with no AWS at all.

## Database configuration

The `postgres` init script (`docker/postgres/initdb/10-roles-and-extension.sh`)
runs once on first cluster init and:

- creates the `mem_app` (RLS-scoped) and `mem_maint` (BYPASSRLS, owner) roles
  using `MEM_APP_PASSWORD` / `MEM_MAINT_PASSWORD`,
- installs the `vector` extension in the app database,
- hands schema ownership to `mem_maint` and grants `mem_app` the DML default
  privileges (mirrors `deploy/postgres/init_roles.sql`).

Keep the passwords in `.env` consistent in three places: `MEM_APP_PASSWORD`,
`MEM_MAINT_PASSWORD`, and the credentials embedded in `MEM_MCP_DB_DSN` /
`MEM_MCP_DB_MAINT_DSN`.

Data persists in the `pgdata` named volume. To wipe and re-init:

```bash
docker compose down -v   # removes pgdata → init script runs fresh next up
```

## Jobs

Containers have no systemd, so the `jobs` service runs
`mem_mcp.jobs.scheduler` — an in-process scheduler that mirrors the
`deploy/systemd/*.timer` cadence for core maintenance jobs and discovers
plugin-declared jobs from the plugin registry. Each fire shells out to the
existing entrypoints as a fresh subprocess (matching systemd `Type=oneshot`).

Run a single job on demand (one-shot container):

```bash
docker compose run --rm jobs job retention_tokens --dry-run
docker compose run --rm jobs plugin-job reminders fire_due_reminders_job
```

The `pg_dump`-based backup timer is intentionally **not** part of the jobs
container; back up Postgres from the host/orchestrator (or `deploy/scripts`).

## Plugins (build-time bake)

Plugins (e.g. `mem-mcp-skill-kite`, `mem-mcp-skill-reminders`) are separate
git repos. They are **baked into the image at build time** so runtime images
are immutable and start fast.

1. List the repos in `deploy/plugins.list` (one git URL per line):

   ```text
   https://github.com/dheemanopc/mem-mcp-skill-kite.git
   https://github.com/dheemanopc/mem-mcp-skill-reminders.git
   ```

2. Build with `INSTALL_PLUGINS=true` and a git credential as a **BuildKit
   secret** (never an `ARG`/`ENV`, which would leak into image history):

   HTTPS / PAT:
   ```bash
   printf '%s' "$GITHUB_TOKEN" > /tmp/git-token
   DOCKER_BUILDKIT=1 docker build \
     --build-arg INSTALL_PLUGINS=true \
     --secret id=github_token,src=/tmp/git-token \
     -t mem-mcp:local .
   ```

   SSH deploy key:
   ```bash
   DOCKER_BUILDKIT=1 docker build \
     --build-arg INSTALL_PLUGINS=true \
     --secret id=ssh_key,src=$HOME/.ssh/id_ed25519 \
     -t mem-mcp:local .
   ```

   Via compose, set `INSTALL_PLUGINS=true` in `.env` and pass the secret:
   ```bash
   INSTALL_PLUGINS=true docker compose build \
     --secret id=github_token,src=/tmp/git-token
   docker compose up
   ```

The build clones each repo into `/opt/mem-mcp-plugins/<name>` and
`pip install`s it into the app venv so its `mem_mcp.skills` entry point is
discovered at startup. A symlink `/opt/mem-mcp-plugins/mem-mcp → /opt/mem-mcp`
makes the plugins' `../mem-mcp` path dependency resolve to the core checkout.
With `INSTALL_PLUGINS=false` (default) you get a clean core-only image.

Verify discovery after start:

```bash
docker compose logs api | grep plugin_discovery_complete
```

## AWS-backed mode

```bash
docker compose -f docker-compose.yml -f docker-compose.aws.yml up
```

The overlay sets `MEM_MCP_SKIP_SSM=""` (falsey → config is loaded from
`/mem-mcp/*` in SSM Parameter Store; explicit env still overrides) and passes
AWS credentials/region to `api`, `migrate`, and `jobs`.

Credentials, pick one:

- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in `.env`;
- mount your shared config — uncomment the `~/.aws` volume in
  `docker-compose.aws.yml`;
- run on EC2/ECS with an instance/task role (boto3 picks it up, no secrets).

**RDS instead of the local Postgres:** point `MEM_MCP_DB_DSN` /
`MEM_MCP_DB_MAINT_DSN` at RDS in `.env`, then drop the bundled database:

```bash
docker compose -f docker-compose.yml -f docker-compose.aws.yml up \
  --scale postgres=0
```

## TLS / production reverse proxy

The bundled `docker/Caddyfile` serves plain HTTP on `:80` for local dev. For a
real host, replace the `:80` site label with your domain(s) and Caddy will
provision TLS via Let's Encrypt automatically — mirror the path split in
`deploy/Caddyfile` (memsys → API, memapp → web with `/api/web/*` + `/auth/*`
to the API).

## Localhost auth + embeddings sidecars

The default localhost stack ships two sidecars that replace the AWS deps:

- **`cognito-local`** (`jagregory/cognito-local:3.21.0`) on port `9229` — fake
  Cognito IdP with `admin@local` pre-seeded.
- **`ollama`** (`ollama/ollama:latest`) on port `11434` — local embeddings via
  the `bge-m3` model (1024-dim, multilingual, matches Titan v2's vector dim
  so no schema migration is needed when swapping providers).

`.env.example` defaults already point both at the sidecars (see
`MEM_MCP_COGNITO_ISSUER_URL`, `MEM_MCP_COGNITO_JWKS_URL`,
`MEM_MCP_EMBEDDINGS_PROVIDER=ollama`, `MEM_MCP_OLLAMA_URL`). Copy `.env.example`
to `.env` and you're done — no edits needed for the auth/embed flow to work.

### First-time admin promotion (two-step boot)

`bootstrap_first_system_admin` runs in `lifespan()` at api boot and looks up
a `tenant_identities` row matching `MEM_MCP_BOOTSTRAP_SYSTEM_ADMIN_EMAIL`. On
a cold DB the row doesn't exist yet (it's created on the first OAuth callback),
so bootstrap returns `{status: "not_found"}` benignly. The flow is:

1. `docker compose up` — stack starts; bootstrap no-ops.
2. Browser → `http://localhost:8000` → `/auth/login` → cognito-local UI →
   sign in as `admin@local` / `Admin@Local2026!` → callback creates the
   tenant + identity rows.
3. `docker compose restart api` — bootstrap runs again, finds the identity,
   grants `SYSTEM_ADMIN`, persists the marker. Idempotent on subsequent boots.

After step 3, `admin@local` has full admin privileges. Subsequent fresh
`compose up` runs skip the bootstrap because the marker is persisted.

### Headless token mint (for `curl` / scripted use)

```bash
eval $(scripts/dev-token.sh)
curl -H "Authorization: Bearer $MEM_MCP_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"tools/list","id":1}' \
     http://localhost:8080/mcp
```

The token is valid for 24 hours per the seeded client config; re-run
`dev-token.sh` to mint a fresh one.

### Using Claude with the localhost server

**Claude Code (CLI):** add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "memsys-local": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

On first call Claude Code follows the 401 → discovers
`/.well-known/oauth-protected-resource` → opens a browser to cognito-local
→ caches the token.

If you'd rather skip the OAuth dance, use the headless token:

```bash
eval $(scripts/dev-token.sh)
# then in ~/.claude.json, add:
#   "headers": { "Authorization": "Bearer ${MEM_MCP_TOKEN}" }
```

**Claude.ai (web):** add `http://localhost:8000` as a custom MCP server in
the integrations settings. The OAuth popup runs in your browser (which can
reach localhost) and the token is cached in your claude.ai session.

### Switching embedding providers

Embedding vectors from `bge-m3` and Titan v2 live in **different semantic
spaces**. Switching `MEM_MCP_EMBEDDINGS_PROVIDER` on a non-empty DB will
break `memory_search` until you re-embed every row. Run:

```bash
docker compose run --rm jobs job embedding_backfill
```

The backfill job uses whichever provider is currently configured. For a
fresh local DB this is moot.

## Troubleshooting

- **`api` restarts / `wait_for_db` timing out** — check the DSN passwords
  match `MEM_APP_PASSWORD`/`MEM_MAINT_PASSWORD`, and that `migrate` succeeded
  (`docker compose logs migrate`).
- **`readyz` 503 with `cognito_jwks: fetch_failed`** — cognito-local sidecar
  isn't reachable from the api container. Confirm
  `MEM_MCP_COGNITO_JWKS_URL=http://cognito-local:9229/...` (the docker
  network hostname, not `localhost`).
- **`readyz` 503 with `bedrock`** — expected if `MEM_MCP_EMBEDDINGS_PROVIDER=
  ollama`: the `bedrock` health check still pings AWS Bedrock. Either set
  `AWS_REGION` + creds (Bedrock check goes green) or accept the 503 on
  `readyz` while `healthz` stays 200. (Future cleanup: gate the Bedrock check
  on provider selection.)
- **`/auth/login` redirects to `memauth.example.com`** — your `.env` still
  has the placeholder values. `cp .env.example .env` again and don't edit
  the cognito-local lines.
- **`ollama pull` keeps re-running** — it's the `ollama-init` one-shot
  service; `restart: no` means it runs once per `docker compose up`. To
  force a model reset: `docker volume rm mem-mcp_ollama_models`.
- **Skip the DB wait** (e.g. external managed DB you trust is up) — set
  `MEM_MCP_SKIP_DB_WAIT=1`.
- **Re-run migrations** — `docker compose run --rm migrate`.
