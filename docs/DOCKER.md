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

## Troubleshooting

- **`api` restarts / `wait_for_db` timing out** — check the DSN passwords
  match `MEM_APP_PASSWORD`/`MEM_MAINT_PASSWORD`, and that `migrate` succeeded
  (`docker compose logs migrate`).
- **`readyz` 503** — expected without AWS: the `bedrock` and `cognito_jwks`
  checks need real AWS. `healthz` (liveness) is always 200 when the process is
  up.
- **Skip the DB wait** (e.g. external managed DB you trust is up) — set
  `MEM_MCP_SKIP_DB_WAIT=1`.
- **Re-run migrations** — `docker compose run --rm migrate`.
