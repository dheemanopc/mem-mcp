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
sudo -u memmcp ~memmcp/.local/bin/poetry install --without dev

log "Installing/updating skill plugins"
if [[ -x "$REPO_DIR/deploy/scripts/install_plugins.sh" ]]; then
    "$REPO_DIR/deploy/scripts/install_plugins.sh" || log "install_plugins.sh failed; continuing"
fi

log "Running migrations"
sudo -u memmcp bash -c "set -a && source $ENV_FILE && set +a && \
  ~memmcp/.local/bin/poetry run alembic upgrade head"

if [[ -d "$REPO_DIR/web" ]]; then
  log "Building Next.js"
  sudo -u memmcp bash -c "cd web && npm ci && npm run build"
fi

log "Re-installing systemd units (timers may have been added)"
for svc in "$REPO_DIR/deploy/systemd/"*.{service,timer}; do
  [[ -e "$svc" ]] && install -m 0644 "$svc" "/etc/systemd/system/$(basename "$svc")"
done

log "Generating plugin job systemd units"
PLUGIN_STAGING=/tmp/mem-mcp-plugin-units
sudo -u memmcp rm -rf "$PLUGIN_STAGING"
# Step 1: generator runs as memmcp (it imports the plugin venv); writes units
# to staging dir and prints the manifest of unit basenames to stdout.
PLUGIN_UNIT_BASES=$(sudo -u memmcp ~memmcp/.local/bin/poetry -C "$REPO_DIR" run \
  python -m deploy.scripts.generate_plugin_units --staging "$PLUGIN_STAGING") || {
  log "plugin unit generation failed; continuing"
  PLUGIN_UNIT_BASES=""
}
# Step 2: install each staged unit + cleanup stale plugin units (as root).
declare -A DESIRED_PLUGIN_UNITS=()
for base in $PLUGIN_UNIT_BASES; do
  install -m 0644 "$PLUGIN_STAGING/${base}.service" "/etc/systemd/system/${base}.service"
  install -m 0644 "$PLUGIN_STAGING/${base}.timer" "/etc/systemd/system/${base}.timer"
  DESIRED_PLUGIN_UNITS["${base}.service"]=1
  DESIRED_PLUGIN_UNITS["${base}.timer"]=1
  log "  installed ${base}.{service,timer}"
done
# Remove stale plugin units no longer in the manifest.
for unit_path in /etc/systemd/system/mem-mcp-plugin-*.service /etc/systemd/system/mem-mcp-plugin-*.timer; do
  [[ -e "$unit_path" ]] || continue
  unit_name=$(basename "$unit_path")
  if [[ -z "${DESIRED_PLUGIN_UNITS[$unit_name]:-}" ]]; then
    log "  removing stale ${unit_name}"
    systemctl disable --now "$unit_name" 2>/dev/null || true
    rm -f "$unit_path"
  fi
done

systemctl daemon-reload
for t in /etc/systemd/system/mem-mcp-*.timer; do
  [[ -e "$t" ]] && systemctl enable --now "$(basename "$t")" || true
done

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
