#!/usr/bin/env bash
# Postgres init script (runs once, on first cluster init, as the superuser).
#
# The pgvector image creates the POSTGRES_DB before running this. Here we:
#   - create the mem_app (RLS-scoped) and mem_maint (BYPASSRLS, owner) roles
#   - install the pgvector extension into the app database
#   - hand schema ownership to mem_maint and grant default privileges to mem_app
#
# Mirrors deploy/postgres/init_roles.sql but reads passwords from env so the
# compose stack stays declarative. Idempotent guards keep re-runs safe.

set -euo pipefail

: "${POSTGRES_DB:?POSTGRES_DB must be set}"
: "${MEM_APP_PASSWORD:?MEM_APP_PASSWORD must be set}"
: "${MEM_MAINT_PASSWORD:?MEM_MAINT_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mem_app') THEN
        CREATE ROLE mem_app LOGIN PASSWORD '${MEM_APP_PASSWORD}';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mem_maint') THEN
        CREATE ROLE mem_maint LOGIN PASSWORD '${MEM_MAINT_PASSWORD}' BYPASSRLS;
    END IF;
END
\$\$;

-- pgvector extension (superuser-only) lives in the app database.
CREATE EXTENSION IF NOT EXISTS vector;

-- mem_maint owns the schema + objects (Alembic runs as mem_maint).
ALTER DATABASE ${POSTGRES_DB} OWNER TO mem_maint;
ALTER SCHEMA public OWNER TO mem_maint;

GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO mem_app;
GRANT USAGE ON SCHEMA public TO mem_app, mem_maint;

-- App role gets DML on everything mem_maint creates (tables created by
-- migrations), matching the production default-privileges grant.
ALTER DEFAULT PRIVILEGES FOR ROLE mem_maint IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mem_app;
ALTER DEFAULT PRIVILEGES FOR ROLE mem_maint IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO mem_app;
SQL

echo "[initdb] mem_app + mem_maint roles, pgvector extension, and grants ready"
