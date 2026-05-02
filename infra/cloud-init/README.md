# Cloud-init Bootstrap

## Overview

This directory contains documentation and iteration targets for the EC2 cloud-init bootstrap process.

## Canonical Source

The actual UserData executed by EC2 is defined in `infra/cfn/nested/060-compute.yaml` via the `UserData` property, which uses `Fn::Base64` and `Fn::Sub` to inject CloudFormation parameters (region, repo_url, repo_ref).

The `user-data.yaml` file here is a reference/documentation copy that matches the inline template in the CFN stack. It is not directly used at runtime.

## What It Does

1. Installs system packages: PostgreSQL 16 + pgvector, Caddy, Node.js 20, Python 3.12 + Poetry, AWS CLI
2. Creates system user `memmcp`
3. Writes instance configuration to `/etc/mem-mcp/instance.env` (CloudFormation parameters)
4. Clones the repo and checks out the specified ref
5. Invokes `deploy/scripts/bootstrap.sh` to complete application setup

## Bootstrap Script

See `deploy/scripts/bootstrap.sh` for the 8-step idempotent bootstrap:
- SSM parameters → environment
- Postgres role/database setup
- Python deps + Alembic migrations
- Next.js build (if web/ present)
- Caddy configuration
- systemd units installation + enable
- Health check
- Success metric emission

## References

- LLD §10.1 — Bootstrap process flow
- LLD §1.2 — Process inventory
- `MEMORY_MCP_BUILD_PLAN_V2.md` §14.1 — Deployment overview
