#!/usr/bin/env bash
# Seed a dedicated service tenant for a headless partner system (e.g. the
# kite trading system) that authenticates to POST /mcp with a Cognito
# client_credentials token.
#
# A client_credentials token has NO user: its `sub` claim equals the app
# client ID, and the Bearer middleware resolves the tenant via
#   tenant_identities.cognito_sub = <token sub>
# plus an oauth_clients row keyed on the token's client_id. Nothing
# auto-creates these in prod (auto-create is localdev-only), so we seed:
#   1. a dedicated tenant (RLS isolates it from every human tenant)
#   2. a personal team + owner role
#   3. a tenant_identities row keyed on the token sub
#   4. an oauth_clients row so the middleware LEFT JOIN sets client_known=true
#
# Row shapes mirror src/mem_mcp/auth/middleware.py
# (_create_personal_team_and_assign + the oauth_clients auto-create insert).
#
# Idempotent: safe to re-run. Guards on the tenant email (unique) and the
# per-tenant personal team so a second run does not duplicate.
#
# Run as the schema OWNER (mem_maint) so RLS is bypassed for the seed —
# same role alembic migrations use.
#
# Usage:
#   KITE_CLIENT_ID=<cognito app client id> \
#     MEM_MCP_DB_MAINT_DSN=postgresql+psycopg://mem_maint:...@host:5432/mem_mcp \
#     scripts/seed-service-tenant.sh
#
# Env:
#   KITE_CLIENT_ID      (required) Cognito app client ID (from SSM /mem-mcp/kite/client_id)
#   KITE_CLIENT_SUB     (default: = KITE_CLIENT_ID) the token `sub`. Override
#                       ONLY if you decoded a real token and sub != client_id.
#   SERVICE_EMAIL       (default: kite-service@internal) synthetic tenant email
#   SERVICE_TEAM_NAME   (default: Kite Service) personal team name
#   MEM_MCP_DB_MAINT_DSN / PSQL_DSN   maintenance DSN (SQLAlchemy or libpq form)

set -euo pipefail

CLIENT_ID="${KITE_CLIENT_ID:?set KITE_CLIENT_ID to the Cognito app client id (SSM /mem-mcp/kite/client_id)}"
CLIENT_SUB="${KITE_CLIENT_SUB:-$CLIENT_ID}"
SERVICE_EMAIL="${SERVICE_EMAIL:-kite-service@internal}"
SERVICE_TEAM_NAME="${SERVICE_TEAM_NAME:-Kite Service}"

# Accept the SQLAlchemy-style maint DSN and normalize to a libpq DSN for psql
# (strip the +psycopg / +asyncpg driver tag).
RAW_DSN="${PSQL_DSN:-${MEM_MCP_DB_MAINT_DSN:?set MEM_MCP_DB_MAINT_DSN or PSQL_DSN to the maintenance DB DSN}}"
DSN="${RAW_DSN/+psycopg/}"
DSN="${DSN/+asyncpg/}"

if ! command -v psql >/dev/null 2>&1; then
    echo "error: psql is required" >&2
    exit 1
fi

echo "==> Seeding service tenant"
echo "    email      : ${SERVICE_EMAIL}"
echo "    team       : ${SERVICE_TEAM_NAME}"
echo "    client_id  : ${CLIENT_ID}"
echo "    cognito_sub: ${CLIENT_SUB}"

psql "$DSN" \
    -v ON_ERROR_STOP=1 \
    -v email="$SERVICE_EMAIL" \
    -v team_name="$SERVICE_TEAM_NAME" \
    -v client_id="$CLIENT_ID" \
    -v sub="$CLIENT_SUB" <<'SQL'
BEGIN;

-- 1. dedicated service tenant (unique on email → idempotent)
INSERT INTO tenants (email, status)
VALUES (:'email', 'active')
ON CONFLICT (email) DO UPDATE SET status = 'active';

-- teams_insert RLS requires created_by_tenant_id = safe_current_tenant_id();
-- set the GUC for this tx (belt-and-suspenders under the owner role).
SELECT set_config('app.current_tenant_id',
                  (SELECT id::text FROM tenants WHERE email = :'email'),
                  true);

-- 2. personal team — guard on absence (teams has no name UNIQUE)
INSERT INTO teams (name, created_by_tenant_id, team_type)
SELECT :'team_name', t.id, 'personal'
FROM tenants t
WHERE t.email = :'email'
  AND NOT EXISTS (
    SELECT 1 FROM teams x
    WHERE x.created_by_tenant_id = t.id AND x.team_type = 'personal'
  );

-- 3. identity keyed on the token sub, pinned to the personal team
INSERT INTO tenant_identities
  (tenant_id, cognito_sub, provider, email, is_primary, default_team_id)
SELECT t.id, :'sub', 'cognito', :'email', true, tm.id
FROM tenants t
JOIN teams tm ON tm.created_by_tenant_id = t.id AND tm.team_type = 'personal'
WHERE t.email = :'email'
ON CONFLICT (cognito_sub) DO UPDATE
  SET default_team_id = EXCLUDED.default_team_id
  WHERE tenant_identities.default_team_id IS NULL;

-- 4. owner role on the team
INSERT INTO team_role_assignments
  (parent_team_id, member_kind, member_id, role_id, status, assigned_by_user_id)
SELECT tm.id, 'user', t.id, rc.id, 'active', t.id
FROM tenants t
JOIN teams tm ON tm.created_by_tenant_id = t.id AND tm.team_type = 'personal'
JOIN roles_catalog rc ON rc.role_key = 'owner' AND rc.plugin_id IS NULL
WHERE t.email = :'email'
ON CONFLICT DO NOTHING;

-- 5. register the oauth client so the middleware LEFT JOIN sets client_known
INSERT INTO oauth_clients
  (id, redirect_uris, scope, registration_payload, review_status)
VALUES (:'client_id', ARRAY[]::text[], 'memory.read memory.write',
        '{}'::jsonb, 'auto_allowed')
ON CONFLICT (id) DO NOTHING;

COMMIT;

-- Report what resolves for this sub (mirrors the middleware resolver query)
SELECT ti.tenant_id, ti.id AS identity_id, ti.default_team_id,
       t.status AS tenant_status,
       (oc.id IS NOT NULL) AS client_known
FROM tenant_identities ti
JOIN tenants t ON t.id = ti.tenant_id
LEFT JOIN oauth_clients oc ON oc.id = :'client_id'
WHERE ti.cognito_sub = :'sub';
SQL

echo "==> Done. The service tenant is ready for client_credentials tokens."
