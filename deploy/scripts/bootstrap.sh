#!/usr/bin/env bash
# mem-mcp bootstrap — runs after cloud-init installs system packages.
# Sets up Postgres, runs Alembic migrations, installs Python+Node app deps,
# configures + starts systemd units. Idempotent: safe to re-run.
#
# Per LLD §10.1.

set -euo pipefail

REPO_DIR=/opt/mem-mcp
ENV_FILE=/etc/mem-mcp/env
INSTANCE_ENV=/etc/mem-mcp/instance.env
DB_NAME=mem_mcp
DB_PG_DATA=/var/lib/postgresql/16/main
LOG=/var/log/mem-mcp-bootstrap.log

# Source instance env (region, repo_url, repo_ref)
[[ -r "$INSTANCE_ENV" ]] && source "$INSTANCE_ENV"

REGION="${MEM_MCP_REGION:-ap-south-1}"

log() {
  echo "[$(date -u +%FT%TZ)] $*"
}

#=============================================================================
# Step 1: Wait for cloud-init base packages to settle
#=============================================================================
log "Step 1: Waiting for cloud-init"
cloud-init status --wait || true

#=============================================================================
# Step 2: Pull SSM parameters into /etc/mem-mcp/env
#=============================================================================
log "Step 2: Reading /mem-mcp/* SSM parameters into $ENV_FILE"
mkdir -p /etc/mem-mcp
chown memmcp:memmcp /etc/mem-mcp

# Get all /mem-mcp/* params (String + SecureString) and write as MEM_MCP_KEY=value lines
# Convert SSM names like /mem-mcp/cognito/user_pool_id → MEM_MCP_COGNITO_USER_POOL_ID
aws --region "$REGION" ssm get-parameters-by-path \
  --path /mem-mcp \
  --recursive --with-decryption \
  --query 'Parameters[].[Name,Value]' \
  --output text \
  | while IFS=$'\t' read -r name value; do
      key="MEM_MCP$(echo "$name" | sed 's|^/mem-mcp||' | tr '[:lower:]/' '[:upper:]_')"
      printf '%s=%q\n' "$key" "$value"
    done > "$ENV_FILE.tmp"

# Add derived/computed values
{
  cat "$ENV_FILE.tmp"
  echo "MEM_MCP_REGION=${REGION}"
  echo "MEM_MCP_DB_DSN=postgresql://mem_app@/${DB_NAME}?host=/var/run/postgresql"
  echo "MEM_MCP_DB_MAINT_DSN=postgresql+psycopg://mem_maint@/${DB_NAME}?host=/var/run/postgresql"
  echo "MEM_MCP_BEDROCK_MODEL_ID=amazon.titan-embed-text-v2:0"
  echo "MEM_MCP_LOG_LEVEL=INFO"
} > "$ENV_FILE"

chmod 0640 "$ENV_FILE"
chown root:memmcp "$ENV_FILE"
rm -f "$ENV_FILE.tmp"

#=============================================================================
# Step 3: Postgres setup (idempotent)
#=============================================================================
log "Step 3: Postgres setup"
systemctl enable --now postgresql

# Wait for Postgres to accept connections
for i in {1..30}; do
  if sudo -u postgres pg_isready -q; then break; fi
  sleep 1
done

# Read SecureString passwords for mem_app, mem_maint
MEM_APP_PASSWORD=$(aws --region "$REGION" ssm get-parameter --with-decryption \
    --name /mem-mcp/db/password --query 'Parameter.Value' --output text)
MEM_MAINT_PASSWORD=$(aws --region "$REGION" ssm get-parameter --with-decryption \
    --name /mem-mcp/db/maint_password --query 'Parameter.Value' --output text)

# Run init_roles.sql (idempotent — uses CREATE ROLE which fails if exists; we wrap in DO blocks)
sudo -u postgres psql -v ON_ERROR_STOP=0 \
  -v mem_app_password="'$MEM_APP_PASSWORD'" \
  -v mem_maint_password="'$MEM_MAINT_PASSWORD'" \
  -c "DO \$\$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mem_app') THEN
          CREATE ROLE mem_app LOGIN PASSWORD '$MEM_APP_PASSWORD';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mem_maint') THEN
          CREATE ROLE mem_maint LOGIN PASSWORD '$MEM_MAINT_PASSWORD' BYPASSRLS;
        END IF;
      END \$\$;"

# Create DB if missing
if ! sudo -u postgres psql -lqt | cut -d\| -f1 | grep -qw "$DB_NAME"; then
  sudo -u postgres createdb -O mem_maint "$DB_NAME"
fi

# Apply default privileges (idempotent)
sudo -u postgres psql -d "$DB_NAME" <<SQL
GRANT CONNECT ON DATABASE $DB_NAME TO mem_app;
GRANT USAGE ON SCHEMA public TO mem_app, mem_maint;
ALTER DEFAULT PRIVILEGES FOR ROLE mem_maint IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mem_app;
ALTER DEFAULT PRIVILEGES FOR ROLE mem_maint IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO mem_app;
SQL

#=============================================================================
# Step 4: Python deps + Alembic upgrade
#=============================================================================
log "Step 4: Python deps + Alembic upgrade"
chown -R memmcp:memmcp "$REPO_DIR"
su - memmcp -c "cd $REPO_DIR && \$HOME/.local/bin/poetry install --no-dev"
su - memmcp -c "cd $REPO_DIR && \
  set -a && source $ENV_FILE && set +a && \
  \$HOME/.local/bin/poetry run alembic upgrade head"

#=============================================================================
# Step 5: Web (Next.js) build
#=============================================================================
log "Step 5: Next.js build (skipped if web/ dir missing — lands later in Phase 8)"
if [[ -d "$REPO_DIR/web" ]]; then
  su - memmcp -c "cd $REPO_DIR/web && pnpm install --frozen-lockfile && pnpm build"
else
  log "  web/ not present yet; skipping (Phase 8)"
fi

#=============================================================================
# Step 6: Caddy config
#=============================================================================
log "Step 6: Caddy config"
install -m 0644 "$REPO_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
systemctl enable --now caddy
systemctl reload caddy

#=============================================================================
# Step 7: systemd units for mem-mcp + mem-web (+ timers)
#=============================================================================
log "Step 7: systemd units"
install -m 0644 "$REPO_DIR/deploy/systemd/mem-mcp.service" /etc/systemd/system/mem-mcp.service
if [[ -f "$REPO_DIR/deploy/systemd/mem-web.service" ]]; then
  install -m 0644 "$REPO_DIR/deploy/systemd/mem-web.service" /etc/systemd/system/mem-web.service
fi
# Retention timers land in later PRs; install whatever exists today
for svc in "$REPO_DIR/deploy/systemd/"*.{service,timer}; do
  [[ -e "$svc" ]] && install -m 0644 "$svc" "/etc/systemd/system/$(basename "$svc")"
done

systemctl daemon-reload
systemctl enable --now mem-mcp.service || true
[[ -f /etc/systemd/system/mem-web.service ]] && systemctl enable --now mem-web.service || true

#=============================================================================
# Step 8: Wait for /healthz and emit success metric
#=============================================================================
log "Step 8: Health checks"
for i in {1..30}; do
  if curl -fs http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
    log "  mem-mcp: healthy"
    break
  fi
  sleep 2
done

# Best-effort: emit success metric so observability stack alarm clears
aws --region "$REGION" cloudwatch put-metric-data \
  --namespace mem-mcp --metric-name bootstrap.success --value 1 || true

log "Bootstrap complete. Tail journalctl -u mem-mcp -f for app logs."
