# Database RLS Smoke Test

## Why

Row-Level Security (RLS) is the critical final layer of tenant isolation. This smoke test verifies that our RLS policies are properly configured and that queries without explicit tenant context return zero rows (fail-closed), preventing accidental data leakage.

Reference: LLD §5.4, spec S-3.

## How to run locally

```bash
# Start a local Postgres (e.g., in Docker)
docker run -d --rm --name mem-test-pg \
  -e POSTGRES_PASSWORD=test \
  -p 5432:5432 \
  pgvector/pgvector:pg16

# Initialize roles and schema
PGPASSWORD=test psql -h localhost -U postgres -d postgres \
  -v mem_app_password='test' \
  -v mem_maint_password='test' \
  -f deploy/postgres/init_roles.sql

# Run migrations to create schema
export MEM_MCP_DB_MAINT_DSN="postgresql+psycopg://postgres:test@localhost/mem_mcp"
poetry run alembic upgrade head

# Run the smoke test
PGPASSWORD=test psql -h localhost -U mem_app -d mem_mcp \
  -f deploy/postgres/smoke_rls.sql
```

Expected output:
```
 tenant_context
────────────────
 (null)

NOTICE:  OK: RLS fail-closed verified - no data leakage without tenant context
```

## How to run on EC2

```bash
# SSH to production VM
ssh -i /path/to/key ubuntu@<elastic-ip>

# Run as mem_app role over Unix socket (no password needed if configured)
psql -h /var/run/postgresql -U mem_app -d mem_mcp \
  -f deploy/postgres/smoke_rls.sql
```

## When to run

- After every schema migration deployment
- After restarting PostgreSQL service
- Before declaring any RLS-critical change complete
- As part of incident response for suspected tenant isolation issues

## What success looks like

- `tenant_context` returns `(null)`
- `NOTICE: OK: RLS fail-closed verified...` is printed
- No errors or exceptions

## What failure looks like

- `RLS LEAK: memories table visible without tenant context` — RLS policy is broken or disabled
- `RLS LEAK: tenant_daily_usage visible without tenant context` — quota-tracking isolation broken
- Any other exception or non-zero row count — investigate immediately

## Troubleshooting

If the test fails:

1. Verify RLS is enabled on both tables:
   ```sql
   SELECT relname, relrowsecurity, relforcerowsecurity 
   FROM pg_class 
   WHERE relname IN ('memories', 'tenant_daily_usage');
   ```
   Both should show `t` (true) for both columns.

2. Check the policy:
   ```sql
   SELECT * FROM pg_policies WHERE tablename IN ('memories', 'tenant_daily_usage');
   ```

3. Verify mem_app does NOT have BYPASSRLS:
   ```sql
   SELECT rolname, bypassrls FROM pg_roles WHERE rolname = 'mem_app';
   ```
   Should show `f` (false) for bypassrls.
