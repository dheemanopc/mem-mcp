#!/usr/bin/env bash
# Local integration-test DB helper.
#
# Wraps `docker compose -f docker-compose.test.yml` + bootstraps the
# mem_app role + runs alembic + exposes MEM_MCP_TEST_DSN. Mirrors the
# CI integration service container so a local run faithfully predicts
# CI behavior.
#
# Subcommands:
#   up    — start container, wait for healthy, create mem_app role, run alembic
#   test  — set DSNs and run `pytest tests/integration` (pass extra args via "$@")
#   psql  — drop into psql against the test DB (handy for debugging fixtures)
#   dsn   — print the export lines for MEM_MCP_TEST_DSN + MEM_MCP_DB_MAINT_DSN
#   down  — stop + remove container AND volume (clean slate)
#   reset — down then up (fresh DB, fresh migrations)
#
# Idempotent: `up` is safe to re-run. `down` removes the named volume so
# data does not survive a cycle.

set -euo pipefail

COMPOSE_FILE="$(cd "$(dirname "$0")/.." && pwd)/docker-compose.test.yml"
TEST_DSN="postgresql://mem_test:mem_test@localhost:5432/mem_test"
MAINT_DSN="postgresql+psycopg://mem_test:mem_test@localhost:5432/mem_test"

cmd_up() {
    if ! command -v docker >/dev/null 2>&1; then
        echo "error: docker is required (install Docker Desktop or docker engine)" >&2
        exit 1
    fi

    echo "==> Starting pgvector container..."
    docker compose -f "$COMPOSE_FILE" up -d

    echo "==> Waiting for Postgres to be healthy..."
    for _ in {1..40}; do
        if docker compose -f "$COMPOSE_FILE" exec -T postgres pg_isready -U mem_test -d mem_test >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    echo "==> Ensuring mem_app role exists..."
    docker compose -f "$COMPOSE_FILE" exec -T -e PGPASSWORD=mem_test postgres \
        psql -h localhost -U mem_test -d mem_test -v ON_ERROR_STOP=1 -c "
        DO \$\$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mem_app') THEN
                CREATE ROLE mem_app NOLOGIN;
            END IF;
        END \$\$;"

    echo "==> Running alembic migrations against test DB..."
    MEM_MCP_DB_MAINT_DSN="$MAINT_DSN" poetry run alembic upgrade head

    echo
    echo "Test DB is ready. Export these to run pytest yourself:"
    cmd_dsn
}

cmd_test() {
    echo "==> Running integration suite..."
    MEM_MCP_TEST_DSN="$TEST_DSN" \
    MEM_MCP_DB_MAINT_DSN="$MAINT_DSN" \
        poetry run pytest tests/integration "$@"
}

cmd_psql() {
    docker compose -f "$COMPOSE_FILE" exec -e PGPASSWORD=mem_test postgres \
        psql -h localhost -U mem_test -d mem_test "$@"
}

cmd_dsn() {
    echo "export MEM_MCP_TEST_DSN=\"$TEST_DSN\""
    echo "export MEM_MCP_DB_MAINT_DSN=\"$MAINT_DSN\""
}

cmd_down() {
    echo "==> Stopping container + removing volume..."
    docker compose -f "$COMPOSE_FILE" down -v
}

cmd_reset() {
    cmd_down
    cmd_up
}

main() {
    case "${1:-}" in
        up) shift; cmd_up "$@" ;;
        test) shift; cmd_test "$@" ;;
        psql) shift; cmd_psql "$@" ;;
        dsn) shift; cmd_dsn "$@" ;;
        down) shift; cmd_down "$@" ;;
        reset) shift; cmd_reset "$@" ;;
        ""|-h|--help|help)
            sed -n '3,18p' "$0"
            ;;
        *)
            echo "error: unknown subcommand: ${1:-}" >&2
            sed -n '3,18p' "$0" >&2
            exit 2
            ;;
    esac
}

main "$@"
