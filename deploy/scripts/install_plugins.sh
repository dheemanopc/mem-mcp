#!/usr/bin/env bash
# Install / update mem-mcp skill plugins listed in /etc/mem-mcp/plugins.list
#
# Idempotent: safe to re-run. Each line in plugins.list is a git URL of a
# repo whose pyproject.toml declares a `mem_mcp.skills` entry point.
#
# Called by bootstrap.sh and deploy.sh after memsys's own poetry install,
# before alembic and service restart.

set -euo pipefail

PLUGINS_LIST=/etc/mem-mcp/plugins.list
PLUGINS_ROOT=/opt/mem-mcp-plugins
POETRY=/home/memmcp/.local/bin/poetry
LOG_PREFIX="install_plugins"

log() { echo "[$(date -u +%FT%TZ)] [${LOG_PREFIX}] $*"; }

if [[ ! -f "$PLUGINS_LIST" ]]; then
    log "no $PLUGINS_LIST found — skipping (treat as no plugins configured)"
    exit 0
fi

mkdir -p "$PLUGINS_ROOT"
chown memmcp:memmcp "$PLUGINS_ROOT"

# Read non-comment, non-blank lines
while IFS= read -r line; do
    # strip whitespace
    url="$(echo "$line" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
    [[ -z "$url" ]] && continue
    [[ "$url" =~ ^# ]] && continue

    # Derive install dir name from the repo path's basename, minus .git
    repo_name="$(basename "$url" .git)"
    install_dir="${PLUGINS_ROOT}/${repo_name}"

    if [[ -d "${install_dir}/.git" ]]; then
        log "updating ${repo_name} (${install_dir})"
        sudo -u memmcp git -C "$install_dir" fetch origin
        sudo -u memmcp git -C "$install_dir" checkout main
        sudo -u memmcp git -C "$install_dir" pull --ff-only origin main
    else
        log "cloning ${repo_name} from ${url}"
        sudo -u memmcp git clone "$url" "$install_dir"
    fi

    # Editable install into memsys's venv
    log "installing ${repo_name} into memsys venv (editable)"
    cd /opt/mem-mcp
    sudo -u memmcp "$POETRY" run pip install -e "$install_dir"
done < "$PLUGINS_LIST"

log "done"
