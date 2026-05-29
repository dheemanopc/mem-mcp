# mem-mcp Deployment Harness

This directory contains scripts and configuration for bootstrapping and deploying mem-mcp on production.

## Deployment scripts

- `bootstrap.sh` — Initial setup after cloud-init (called by CFT user-data). Idempotent.
- `deploy.sh` — In-place deploy (pull + install + migrate + restart). Operator-run.
- `install_plugins.sh` — Install/update plugins from `plugins.list`. Called by both bootstrap and deploy.
- `destroy.sh` — Cleanup after stack deletion (idempotent).

## Plugin System (v1)

### Overview

Plugins are separate Git repositories (e.g., `mem-mcp-skill-kite`, `mem-mcp-skill-reminders`) that are cloned and installed as editable packages into the memsys venv.

### Configuration

Plugins to install are specified in `/etc/mem-mcp/plugins.list` — one Git URL per line.

Example:
```
# /etc/mem-mcp/plugins.list
git@github.com:dheemanopc/mem-mcp-skill-kite.git
git@github.com:dheemanopc/mem-mcp-skill-reminders.git
```

See `plugins.list.example` for full format (comments, blank lines OK).

### Plugin installation

- **Location**: `/opt/mem-mcp-plugins/<repo-name>/`
- **Process**: `git clone` (or `git pull --ff-only` if exists), then `pip install -e` into memsys's venv
- **Idempotent**: Safe to re-run. Existing repos are updated; missing repos are cloned.
- **Permissions**: All plugin dirs owned by `memmcp:memmcp`
- **When**: Automatically during `bootstrap.sh` (Step 4b) and `deploy.sh`
- **Failure handling**: Plugin install failures are **non-fatal**. If a plugin fails to install, core memsys continues. Check logs for details.

### Authentication

The deploy host (EC2 instance) must be able to clone the configured plugin repos. For private repos:

- **SSH**: Configure SSH keys in the instance IAM role (e.g., CodeCommit with assumed credentials) or place deploy keys in `~memmcp/.ssh/config`.
- **HTTPS with PAT**: Store GitHub PAT in SSM `/mem-mcp/git/https_token` and configure `git config credential.helper` on the instance.

No auth credentials are stored in scripts. Deploy host auth is configured outside this PR (security team / ops).

### Manual plugin update

To manually trigger plugin installation/update (without a full deploy):

```bash
sudo /opt/mem-mcp/deploy/scripts/install_plugins.sh
```

### Plugin migrations (limitation in v1)

If a plugin ships Alembic migrations under its own `alembic/` directory, those migrations are **NOT auto-applied**. The operator must run them manually:

```bash
cd /opt/mem-mcp
sudo -u memmcp poetry run alembic -c <plugin-path>/alembic.ini upgrade head
```

Per-plugin migration auto-apply is a Phase 2 follow-up.

## Other directories

- `postgres/` — PG auth config (`pg_hba.conf`, `pg_ident.conf`)
- `systemd/` — systemd service and timer units
- `Caddyfile` — Reverse proxy config (TLS, routing)
