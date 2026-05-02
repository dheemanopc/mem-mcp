#!/usr/bin/env bash
# mem-mcp in-place deploy — operator runs after pushing new commits.
# Per LLD §10.2.

set -euo pipefail

REPO_DIR=/opt/mem-mcp
ENV_FILE=/etc/mem-mcp/env
LOG=/var/log/mem-mcp-deploy.log
REF="${1:-main}"

log() { echo "[$(date -u +%FT%TZ)] $*"; }

cd "$REPO_DIR"
log "Pulling $REF"
sudo -u memmcp git fetch origin
sudo -u memmcp git checkout "$REF"
sudo -u memmcp git pull --ff-only origin "$REF"

log "Installing Python deps"
sudo -u memmcp ~memmcp/.local/bin/poetry install --no-dev

log "Running migrations"
sudo -u memmcp bash -c "set -a && source $ENV_FILE && set +a && \
  ~memmcp/.local/bin/poetry run alembic upgrade head"

if [[ -d "$REPO_DIR/web" ]]; then
  log "Building Next.js"
  sudo -u memmcp bash -c "cd web && pnpm install --frozen-lockfile && pnpm build"
fi

log "Restarting services"
systemctl restart mem-mcp.service
[[ -f /etc/systemd/system/mem-web.service ]] && systemctl restart mem-web.service || true

log "Waiting for /readyz (30s)"
for i in {1..30}; do
  if curl -fs http://127.0.0.1:8080/readyz >/dev/null 2>&1; then
    log "Deploy successful (ref=$REF)"
    aws --region "${MEM_MCP_REGION:-ap-south-1}" cloudwatch put-metric-data \
      --namespace mem-mcp --metric-name deploy.success --value 1 || true
    exit 0
  fi
  sleep 1
done

log "Deploy: /readyz did not return 200 within 30s. Check journalctl -u mem-mcp"
exit 1
