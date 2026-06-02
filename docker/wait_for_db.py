"""Block until the database accepts a connection (bounded retry).

Reads the maintenance DSN from ``MEM_MCP_DB_MAINT_DSN`` (the same var Alembic
uses), normalizes the SQLAlchemy ``postgresql+psycopg://`` form to a libpq DSN,
and retries ``SELECT 1`` with linear backoff. Exits 0 on success, non-zero if
the database never becomes reachable within the timeout.

Tuning via env:
  MEM_MCP_DB_WAIT_TIMEOUT  total seconds to wait (default 60)
  MEM_MCP_DB_WAIT_INTERVAL  seconds between attempts (default 2)
"""

from __future__ import annotations

import os
import sys
import time


def _libpq_dsn() -> str:
    dsn = os.environ.get("MEM_MCP_DB_MAINT_DSN") or os.environ.get("MEM_MCP_DB_DSN")
    if not dsn:
        print("[wait_for_db] no MEM_MCP_DB_MAINT_DSN / MEM_MCP_DB_DSN set", file=sys.stderr)
        sys.exit(2)
    # psycopg/libpq want plain postgresql://, not SQLAlchemy's +psycopg form.
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def main() -> int:
    dsn = _libpq_dsn()
    timeout = float(os.environ.get("MEM_MCP_DB_WAIT_TIMEOUT", "60"))
    interval = float(os.environ.get("MEM_MCP_DB_WAIT_INTERVAL", "2"))

    import psycopg

    deadline = time.monotonic() + timeout
    attempt = 0
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        attempt += 1
        try:
            with psycopg.connect(dsn, connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            print(f"[wait_for_db] database ready after {attempt} attempt(s)")
            return 0
        except Exception as exc:  # any connection error is retryable
            last_err = exc
            print(f"[wait_for_db] attempt {attempt} failed: {exc}", file=sys.stderr)
            time.sleep(interval)

    print(f"[wait_for_db] database not reachable within {timeout}s: {last_err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
