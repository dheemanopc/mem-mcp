# mem-mcp application image (API + jobs + plugin jobs + migrations).
#
# One image, many roles — the entrypoint dispatches on the first arg:
#   api         → uvicorn (FastAPI service, port 8080)
#   scheduler   → in-process job scheduler (replaces systemd timers)
#   migrate     → alembic upgrade head
#   job <name>  → python -m mem_mcp.jobs <name>
#   plugin-job <id> <name> → python -m mem_mcp.jobs.plugin_job <id> <name>
#
# Plugins are baked at build time (BuildKit). Enable with:
#   DOCKER_BUILDKIT=1 docker build \
#     --build-arg INSTALL_PLUGINS=true \
#     --secret id=github_token,src=$HOME/.mem-mcp-git-token \
#     -t mem-mcp .
# or via SSH:
#   --secret id=ssh_key,src=$HOME/.ssh/id_ed25519
# The repos to install come from PLUGINS_LIST (default deploy/plugins.list).
#
# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------------------
# Stage 1: builder — install deps into an in-project venv, optionally bake plugins
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=2.3.3 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_NO_INTERACTION=1

# Build toolchain + libpq headers (asyncpg/psycopg) + git/ssh (plugin clones)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        git \
        openssh-client \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install "poetry==${POETRY_VERSION}"

WORKDIR /opt/mem-mcp

# Dependency layer — cached unless pyproject/lock change.
COPY pyproject.toml poetry.lock README.md ./
RUN poetry install --without dev --no-root

# Application source + migrations.
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
COPY deploy ./deploy

# Install the project itself into the venv.
RUN poetry install --without dev

# --- Optional: build-time plugin bake -------------------------------------
ARG INSTALL_PLUGINS=false
ARG PLUGINS_LIST=deploy/plugins.list
ENV INSTALL_PLUGINS=${INSTALL_PLUGINS} \
    PLUGINS_LIST=${PLUGINS_LIST}
# Secrets are optional; the script no-ops if INSTALL_PLUGINS!=true.
RUN --mount=type=secret,id=github_token,required=false \
    --mount=type=secret,id=ssh_key,required=false \
    bash deploy/scripts/install_plugins_build.sh

# ---------------------------------------------------------------------------
# Stage 2: runtime — slim image with just the venv + source + plugins
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/mem-mcp/.venv/bin:${PATH}" \
    MEM_MCP_REGION=ap-south-1

# Runtime libs only: libpq for psycopg/asyncpg, postgresql-client for pg_isready.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        postgresql-client \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 memmcp \
    && useradd --system --uid 10001 --gid memmcp --home-dir /opt/mem-mcp memmcp

WORKDIR /opt/mem-mcp

# Copy the built app (venv + source + alembic + deploy) from the builder.
COPY --from=builder --chown=memmcp:memmcp /opt/mem-mcp /opt/mem-mcp
# Baked plugins (if any) live at /opt/mem-mcp-plugins; editable installs in the
# venv reference these paths, so they must exist at the same location.
COPY --from=builder --chown=memmcp:memmcp /opt/mem-mcp-plugins /opt/mem-mcp-plugins

COPY --chown=memmcp:memmcp docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --chown=memmcp:memmcp docker/wait_for_db.py /usr/local/bin/wait_for_db.py
RUN chmod +x /usr/local/bin/entrypoint.sh

USER memmcp
EXPOSE 8080

# Liveness probe target (compose/orchestrators can override).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["api"]
