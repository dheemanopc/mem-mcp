"""Tests for deploy/scripts/seed_invite.py (T-4.11)."""

from __future__ import annotations

import sys
from datetime import UTC
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# Add deploy/scripts to path so we can import seed_invite
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "deploy" / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))


@pytest.fixture(autouse=True)
def _reset_module() -> None:
    if "seed_invite" in sys.modules:
        del sys.modules["seed_invite"]
    import seed_invite  # noqa: F401  # type: ignore[import-not-found]


@pytest.fixture
def fake_conn() -> AsyncMock:
    """An AsyncMock standing in for asyncpg.Connection."""
    conn = AsyncMock()
    conn.close = AsyncMock(return_value=None)
    return conn


def _patch_connect(fake_conn: AsyncMock) -> Any:
    """Patches seed_invite._connect to return our fake conn."""
    import seed_invite

    return patch.object(seed_invite, "_connect", AsyncMock(return_value=fake_conn))


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class TestAffectedCount:
    def test_update_one(self) -> None:
        import seed_invite

        assert seed_invite._affected_count("UPDATE 1") == 1

    def test_delete_zero(self) -> None:
        import seed_invite

        assert seed_invite._affected_count("DELETE 0") == 0

    def test_unparseable_returns_zero(self) -> None:
        import seed_invite

        assert seed_invite._affected_count("?") == 0


# --------------------------------------------------------------------------
# add
# --------------------------------------------------------------------------


class TestAdd:
    def test_inserts_and_lowercases_email(self, fake_conn: AsyncMock) -> None:
        import seed_invite

        fake_conn.fetchrow.return_value = {
            "email": "anand@dheemantech.com",
            "invited_by": "ops",
            "invited_at": "2026-05-03",
            "consumed_at": None,
            "notes": "founder",
        }
        with _patch_connect(fake_conn):
            rc = seed_invite.main(
                ["add", "ANAND@DheemanTech.COM", "--invited-by", "ops", "--notes", "founder"]
            )
        assert rc == 0
        # Verify SQL + lowercased email
        call_args = fake_conn.fetchrow.call_args
        sql, email, by, notes = call_args.args
        assert "INSERT INTO invited_emails" in sql
        assert "ON CONFLICT" in sql
        assert email == "anand@dheemantech.com"
        assert by == "ops"
        assert notes == "founder"

    def test_optional_args_none(self, fake_conn: AsyncMock) -> None:
        import seed_invite

        fake_conn.fetchrow.return_value = {
            "email": "x@y.com",
            "invited_by": None,
            "invited_at": "...",
            "consumed_at": None,
            "notes": None,
        }
        with _patch_connect(fake_conn):
            rc = seed_invite.main(["add", "x@y.com"])
        assert rc == 0


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------


class TestList:
    def test_empty(self, fake_conn: AsyncMock, capsys: pytest.CaptureFixture[str]) -> None:
        import seed_invite

        fake_conn.fetch.return_value = []
        with _patch_connect(fake_conn):
            rc = seed_invite.main(["list"])
        assert rc == 0
        assert "no invited emails" in capsys.readouterr().out

    def test_prints_rows(self, fake_conn: AsyncMock, capsys: pytest.CaptureFixture[str]) -> None:
        import seed_invite

        fake_conn.fetch.return_value = [
            {
                "email": "a@b.com",
                "invited_by": "ops",
                "invited_at": "x",
                "consumed_at": None,
                "notes": "n",
            },
        ]
        with _patch_connect(fake_conn):
            seed_invite.main(["list"])
        out = capsys.readouterr().out
        assert "a@b.com" in out
        assert "ops" in out


# --------------------------------------------------------------------------
# show
# --------------------------------------------------------------------------


class TestShow:
    def test_found(self, fake_conn: AsyncMock, capsys: pytest.CaptureFixture[str]) -> None:
        import seed_invite

        fake_conn.fetchrow.return_value = {
            "email": "x@y.com",
            "invited_by": None,
            "invited_at": "...",
            "consumed_at": None,
            "notes": None,
        }
        with _patch_connect(fake_conn):
            rc = seed_invite.main(["show", "x@y.com"])
        assert rc == 0
        assert "x@y.com" in capsys.readouterr().out

    def test_not_found_returns_1(self, fake_conn: AsyncMock) -> None:
        import seed_invite

        fake_conn.fetchrow.return_value = None
        with _patch_connect(fake_conn):
            rc = seed_invite.main(["show", "nobody@example.com"])
        assert rc == 1


# --------------------------------------------------------------------------
# revoke
# --------------------------------------------------------------------------


class TestRevoke:
    def test_marks_with_sentinel(self, fake_conn: AsyncMock) -> None:
        from datetime import datetime

        import seed_invite

        fake_conn.execute.return_value = "UPDATE 1"
        with _patch_connect(fake_conn):
            rc = seed_invite.main(["revoke", "x@y.com"])
        assert rc == 0
        sql, sentinel, email = fake_conn.execute.call_args.args
        assert "UPDATE invited_emails SET consumed_at" in sql
        assert sentinel == datetime(1970, 1, 1, tzinfo=UTC)
        assert email == "x@y.com"

    def test_not_found_returns_1(self, fake_conn: AsyncMock) -> None:
        import seed_invite

        fake_conn.execute.return_value = "UPDATE 0"
        with _patch_connect(fake_conn):
            rc = seed_invite.main(["revoke", "nobody@example.com"])
        assert rc == 1


# --------------------------------------------------------------------------
# delete
# --------------------------------------------------------------------------


class TestDelete:
    def test_deletes(self, fake_conn: AsyncMock) -> None:
        import seed_invite

        fake_conn.execute.return_value = "DELETE 1"
        with _patch_connect(fake_conn):
            rc = seed_invite.main(["delete", "x@y.com"])
        assert rc == 0

    def test_not_found_returns_1(self, fake_conn: AsyncMock) -> None:
        import seed_invite

        fake_conn.execute.return_value = "DELETE 0"
        with _patch_connect(fake_conn):
            rc = seed_invite.main(["delete", "nobody@example.com"])
        assert rc == 1


# --------------------------------------------------------------------------
# Argparse
# --------------------------------------------------------------------------


class TestArgparse:
    def test_no_subcommand_errors(self) -> None:
        import seed_invite

        with pytest.raises(SystemExit):
            seed_invite.main([])

    def test_unknown_subcommand_errors(self) -> None:
        import seed_invite

        with pytest.raises(SystemExit):
            seed_invite.main(["nonexistent_cmd"])
