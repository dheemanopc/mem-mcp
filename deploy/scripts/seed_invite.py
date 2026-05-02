#!/usr/bin/env python3
"""Operator CLI for managing the invited_emails allowlist.

Usage:
    seed_invite.py add anand@dheemantech.com --invited-by ops --notes "founder"
    seed_invite.py list
    seed_invite.py show anand@dheemantech.com
    seed_invite.py revoke anand@dheemantech.com
    seed_invite.py delete anand@dheemantech.com

Requires MEM_MCP_DB_MAINT_DSN env var (mem_maint DB role; uses BYPASSRLS).

Per T-4.11 (LLD §4.10.2 + spec §7.3): the PreSignUp Lambda calls
/internal/check_invite which checks invited_emails. This script is how
the operator populates that table.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg


SENTINEL_REVOKED = "1970-01-01T00:00:00+00:00"


async def _connect() -> asyncpg.Connection:
    """Open a single connection (CLI is short-lived; no pool needed)."""
    import asyncpg

    dsn = os.environ.get("MEM_MCP_DB_MAINT_DSN")
    if not dsn:
        raise SystemExit("MEM_MCP_DB_MAINT_DSN env var required")
    # Strip the +psycopg / +asyncpg suffix if present (SQLAlchemy DSN style)
    if "+" in dsn.split("://", 1)[0]:
        scheme, rest = dsn.split("://", 1)
        scheme = scheme.split("+", 1)[0]  # postgresql+psycopg → postgresql
        dsn = f"{scheme}://{rest}"
    return await asyncpg.connect(dsn)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


async def cmd_add(args: argparse.Namespace) -> int:
    email = args.email.lower()
    conn = await _connect()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO invited_emails (email, invited_by, notes)
            VALUES ($1, $2, $3)
            ON CONFLICT (email) DO UPDATE
            SET invited_by = EXCLUDED.invited_by,
                notes = EXCLUDED.notes
            RETURNING email, invited_by, invited_at, consumed_at, notes
            """,
            email,
            args.invited_by,
            args.notes,
        )
    finally:
        await conn.close()
    _print_row(dict(row) if row else {})
    return 0


async def cmd_list(args: argparse.Namespace) -> int:
    conn = await _connect()
    try:
        rows = await conn.fetch(
            """
            SELECT email, invited_by, invited_at, consumed_at, notes
            FROM invited_emails
            ORDER BY invited_at DESC
            """
        )
    finally:
        await conn.close()
    if not rows:
        print("(no invited emails)")
        return 0
    _print_table([dict(r) for r in rows])
    return 0


async def cmd_show(args: argparse.Namespace) -> int:
    conn = await _connect()
    try:
        row = await conn.fetchrow(
            "SELECT email, invited_by, invited_at, consumed_at, notes FROM invited_emails WHERE email = $1",
            args.email.lower(),
        )
    finally:
        await conn.close()
    if row is None:
        print(f"(not found: {args.email})", file=sys.stderr)
        return 1
    _print_row(dict(row))
    return 0


async def cmd_revoke(args: argparse.Namespace) -> int:
    """Mark as consumed via sentinel timestamp; row stays so audit history is preserved."""
    from datetime import datetime, timezone

    sentinel = datetime(1970, 1, 1, tzinfo=timezone.utc)
    conn = await _connect()
    try:
        result = await conn.execute(
            "UPDATE invited_emails SET consumed_at = $1 WHERE email = $2",
            sentinel,
            args.email.lower(),
        )
    finally:
        await conn.close()
    affected = _affected_count(result)
    if affected == 0:
        print(f"(not found: {args.email})", file=sys.stderr)
        return 1
    print(f"revoked: {args.email}")
    return 0


async def cmd_delete(args: argparse.Namespace) -> int:
    conn = await _connect()
    try:
        result = await conn.execute(
            "DELETE FROM invited_emails WHERE email = $1",
            args.email.lower(),
        )
    finally:
        await conn.close()
    affected = _affected_count(result)
    if affected == 0:
        print(f"(not found: {args.email})", file=sys.stderr)
        return 1
    print(f"deleted: {args.email}")
    return 0


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _affected_count(result: str) -> int:
    """Parse asyncpg execute result like 'UPDATE 1' or 'DELETE 0'."""
    parts = result.split()
    if len(parts) >= 2 and parts[-1].isdigit():
        return int(parts[-1])
    return 0


def _print_row(row: dict[str, Any]) -> None:
    if not row:
        return
    for k, v in row.items():
        print(f"  {k:<14} {v}")


def _print_table(rows: list[dict[str, Any]]) -> None:
    headers = ["email", "invited_by", "invited_at", "consumed_at", "notes"]
    widths = {h: len(h) for h in headers}
    for r in rows:
        for h in headers:
            widths[h] = max(widths[h], len(str(r.get(h, ""))))
    line = "  ".join(h.ljust(widths[h]) for h in headers)
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(str(r.get(h, "")).ljust(widths[h]) for h in headers))


# --------------------------------------------------------------------------
# Argparse
# --------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seed_invite",
        description="Manage the invited_emails allowlist (mem-mcp T-4.11).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Insert or update an invited email")
    p_add.add_argument("email")
    p_add.add_argument("--invited-by", default=None, help="Operator name/handle")
    p_add.add_argument("--notes", default=None)
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="Print all invited emails")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="Print one invited email")
    p_show.add_argument("email")
    p_show.set_defaults(func=cmd_show)

    p_revoke = sub.add_parser("revoke", help="Mark as consumed (sentinel) so it can no longer be redeemed")
    p_revoke.add_argument("email")
    p_revoke.set_defaults(func=cmd_revoke)

    p_delete = sub.add_parser("delete", help="Hard-delete the row")
    p_delete.add_argument("email")
    p_delete.set_defaults(func=cmd_delete)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
