#!/usr/bin/env bash
# Build-time plugin bake for the Docker image.
#
# Reads a plugins list (one git URL per line; # comments / blank lines OK),
# clones each repo into /opt/mem-mcp-plugins/<name>, and pip-installs it into
# the in-project venv so its `mem_mcp.skills` entry point is discovered at
# runtime. Mirrors deploy/scripts/install_plugins.sh but for an immutable
# image (no sudo, no systemd, git auth via BuildKit secrets).
#
# Invoked from the Dockerfile builder stage:
#   RUN --mount=type=secret,id=github_token,required=false \
#       --mount=type=secret,id=ssh_key,required=false \
#       bash deploy/scripts/install_plugins_build.sh
#
# Controlled by env:
#   INSTALL_PLUGINS  "true" to actually install; anything else = no-op
#   PLUGINS_LIST     path to the list file (default deploy/plugins.list)
#
# Git auth (optional, pick one):
#   - BuildKit secret id=github_token → used for https://<token>@github.com/...
#   - BuildKit secret id=ssh_key      → used as the SSH deploy key for git@github
#
# The list dir /opt/mem-mcp-plugins is always created (even with no plugins)
# so the Dockerfile's COPY of it never fails.

set -euo pipefail

PLUGINS_ROOT=/opt/mem-mcp-plugins
CORE_DIR=/opt/mem-mcp
PLUGINS_LIST="${PLUGINS_LIST:-deploy/plugins.list}"
PIP="${CORE_DIR}/.venv/bin/pip"

log() { echo "[install_plugins_build] $*"; }

# Always create the root + a symlink so a plugin's `../mem-mcp` path dependency
# resolves to the core checkout regardless of pip's path-resolution base.
mkdir -p "$PLUGINS_ROOT"
ln -sfn "$CORE_DIR" "${PLUGINS_ROOT}/mem-mcp"

if [[ "${INSTALL_PLUGINS:-false}" != "true" ]]; then
    log "INSTALL_PLUGINS != true — skipping plugin bake (core-only image)"
    exit 0
fi

if [[ ! -f "$PLUGINS_LIST" ]]; then
    log "no plugins list at '$PLUGINS_LIST' — nothing to install"
    exit 0
fi

# --- Configure git auth from BuildKit secrets (if provided) ----------------
if [[ -s /run/secrets/github_token ]]; then
    log "configuring HTTPS git auth from github_token secret"
    TOKEN="$(tr -d '\r\n' < /run/secrets/github_token)"
    git config --global url."https://${TOKEN}@github.com/".insteadOf "https://github.com/"
    git config --global url."https://${TOKEN}@github.com/".insteadOf "git@github.com:"
elif [[ -s /run/secrets/ssh_key ]]; then
    log "configuring SSH git auth from ssh_key secret"
    mkdir -p /root/.ssh
    install -m 0600 /run/secrets/ssh_key /root/.ssh/id_plugin
    ssh-keyscan -t rsa,ed25519 github.com >> /root/.ssh/known_hosts 2>/dev/null || true
    export GIT_SSH_COMMAND="ssh -i /root/.ssh/id_plugin -o IdentitiesOnly=yes"
fi

while IFS= read -r raw; do
    url="$(echo "$raw" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
    [[ -z "$url" || "$url" =~ ^# ]] && continue

    repo_name="$(basename "$url" .git)"
    install_dir="${PLUGINS_ROOT}/${repo_name}"

    log "cloning ${repo_name} from ${url}"
    git clone --depth 1 "$url" "$install_dir"

    log "installing ${repo_name} into venv"
    # cd into core so a cwd-relative `../mem-mcp` path dep also resolves; the
    # symlink above covers the file-relative case.
    (cd "$CORE_DIR" && "$PIP" install "$install_dir")
done < "$PLUGINS_LIST"

log "done"
