# mem-mcp Deployment Harness

This directory contains scripts and configuration for bootstrapping and deploying mem-mcp on production.

## Deployment scripts

- `bootstrap.sh` — Initial setup after cloud-init (called by CFT user-data). Idempotent.
- `deploy.sh` — In-place deploy (pull + install + migrate + restart). Operator-run.
- `install_plugins.sh` — Install/update plugins from `plugins.list`. Called by both bootstrap and deploy.
- `destroy.sh` — Cleanup after stack deletion (idempotent).

## Triggering a prod deploy (the operator workflow)

`deploy.sh` runs ON the prod EC2 box. From a local workstation, invoke it via AWS SSM (no SSH required, no inbound port):

```bash
aws ssm send-command \
  --instance-ids <prod-instance-id> \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["sudo /opt/mem-mcp/deploy/scripts/deploy.sh main"]' \
  --output text
```

Replace `<prod-instance-id>` with the EC2 instance ID (find via `aws ec2 describe-instances --filters Name=tag:Name,Values=mem-mcp-*`). The ref argument (`main`) is what `deploy.sh` checks out before the install chain; pass any git ref to deploy a specific commit / tag / branch.

To watch progress (one-shot):

```bash
COMMAND_ID=$(aws ssm send-command \
  --instance-ids <prod-instance-id> \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["sudo /opt/mem-mcp/deploy/scripts/deploy.sh main"]' \
  --query "Command.CommandId" --output text)

aws ssm wait command-executed --command-id "$COMMAND_ID" --instance-id <prod-instance-id>
aws ssm get-command-invocation --command-id "$COMMAND_ID" --instance-id <prod-instance-id> \
  --query "StandardOutputContent" --output text
```

Deploy script flow (single SSM call drives all of it):
1. `git fetch && git checkout <ref> && git pull --ff-only` in `/opt/mem-mcp` (as `memmcp`).
2. `poetry install --without dev` (memsys-core deps).
3. `install_plugins.sh` — clones/pulls each repo in `/etc/mem-mcp/plugins.list`, `pip install -e` into the memsys venv.
4. `alembic upgrade head` (memsys-core migrations).
5. Next.js rebuild if `web/` present.
6. Re-install systemd `.service` / `.timer` units; generate plugin job units; `systemctl daemon-reload`; enable timers; restart Type=simple drain daemons so they pick up new code.

Logs:
- Operator-visible stdout: returned by `get-command-invocation` above.
- On the box: `/var/log/mem-mcp-deploy.log` (timestamped per-run), `journalctl -u mem-mcp.service`.

Verifying a deploy:
- `curl -s https://memsys.dheemantech.in/readyz` → `{"status":"ok",...}` (db / bedrock / cognito_jwks all `ok`).
- Plugin discovery (T1-parity bar): check the `plugin_discovery_complete` line in journal: `count=N, ids=[...]` includes the expected plugin IDs.
- For plugin code changes: re-confirm the plugin's tools appear in `tools/list` on the MCP endpoint.

Why SSM (vs SSH):
- No inbound SSH port to keep open; instance role gates access via IAM, not network.
- Audit trail in CloudTrail / SSM run history.
- Same command works from anywhere with AWS credentials — no key distribution.

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
