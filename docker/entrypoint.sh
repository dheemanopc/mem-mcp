#!/usr/bin/env bash
# Dispatch entrypoint for the mem-mcp app image.
#
# Usage (first arg selects the role):
#   api                      → uvicorn FastAPI service on 0.0.0.0:8080
#   scheduler                → in-process job scheduler (replaces systemd timers)
#   migrate                  → alembic upgrade head, then exit
#   job <name> [--dry-run]   → run one core maintenance job, then exit
#   plugin-job <id> <name>   → run one plugin job, then exit
#   <anything else>          → exec it verbatim (e.g. `bash`)
#
# Roles that need the DB wait for it first (bounded retry) so compose
# ordering races don't crash the container on a cold start.

set -euo pipefail

wait_for_db() {
    if [[ "${MEM_MCP_SKIP_DB_WAIT:-}" == "1" ]]; then
        return 0
    fi
    echo "[entrypoint] waiting for database..."
    python /usr/local/bin/wait_for_db.py
}

cmd="${1:-api}"
shift || true

case "$cmd" in
    api)
        wait_for_db
        echo "[entrypoint] starting uvicorn (mem_mcp.main:app)"
        # --workers 1 required until session_cache moves to DB/Redis (memsys d6baba0c).
        # forwarded-allow-ips '*' because we sit behind Caddy on the compose network.
        exec uvicorn mem_mcp.main:app \
            --host 0.0.0.0 --port 8080 \
            --proxy-headers --forwarded-allow-ips '*' \
            --workers 1
        ;;
    scheduler)
        wait_for_db
        echo "[entrypoint] starting job scheduler (mem_mcp.jobs.scheduler)"
        exec python -m mem_mcp.jobs.scheduler
        ;;
    migrate)
        wait_for_db
        echo "[entrypoint] running alembic upgrade head"
        exec alembic upgrade head
        ;;
    job)
        wait_for_db
        echo "[entrypoint] running core job: $*"
        exec python -m mem_mcp.jobs "$@"
        ;;
    plugin-job)
        wait_for_db
        echo "[entrypoint] running plugin job: $*"
        exec python -m mem_mcp.jobs.plugin_job "$@"
        ;;
    *)
        exec "$cmd" "$@"
        ;;
esac
