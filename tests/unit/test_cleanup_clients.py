"""Tests for mem_mcp.jobs.cleanup_clients (T-4.9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from mem_mcp.jobs.cleanup_clients import _classify, run

# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeLister:
    def __init__(self, candidates: list[dict[str, Any]]) -> None:
        self.candidates = candidates

    async def list_candidates(self) -> list[dict[str, Any]]:
        return list(self.candidates)


class FakeCognitoDeleter:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.fail_for = fail_for or set()
        self.calls: list[str] = []

    async def delete_user_pool_client(self, client_id: str) -> None:
        self.calls.append(client_id)
        if client_id in self.fail_for:
            raise RuntimeError(f"cognito refused {client_id}")


class FakeTombstone:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.fail_for = fail_for or set()
        self.calls: list[str] = []

    async def mark_deleted(self, client_id: str) -> None:
        self.calls.append(client_id)
        if client_id in self.fail_for:
            raise RuntimeError(f"db refused {client_id}")


def _candidate(
    client_id: str,
    *,
    last_used_at: datetime | None = None,
    created_at: datetime | None = None,
    disabled: bool = False,
    client_name: str = "Some Client",
) -> dict[str, Any]:
    return {
        "id": client_id,
        "client_name": client_name,
        "last_used_at": last_used_at,
        "created_at": created_at or datetime.now(tz=UTC) - timedelta(days=1),
        "disabled": disabled,
    }


# --------------------------------------------------------------------------
# _classify
# --------------------------------------------------------------------------


class TestClassify:
    def test_disabled(self) -> None:
        assert _classify({"disabled": True, "last_used_at": None}) == "disabled"

    def test_disabled_overrides_other(self) -> None:
        assert _classify({"disabled": True, "last_used_at": datetime.now(tz=UTC)}) == "disabled"

    def test_never_used(self) -> None:
        assert _classify({"disabled": False, "last_used_at": None}) == "never_used"

    def test_stale(self) -> None:
        old = datetime.now(tz=UTC) - timedelta(days=120)
        assert _classify({"disabled": False, "last_used_at": old}) == "stale_90d"


# --------------------------------------------------------------------------
# run() — happy path
# --------------------------------------------------------------------------


class TestRunHappy:
    @pytest.mark.asyncio
    async def test_no_candidates_no_calls(self) -> None:
        cog = FakeCognitoDeleter()
        tomb = FakeTombstone()
        n = await run(lister=FakeLister([]), cognito_deleter=cog, tombstone=tomb)
        assert n == 0
        assert cog.calls == []
        assert tomb.calls == []

    @pytest.mark.asyncio
    async def test_deletes_each_candidate(self) -> None:
        candidates = [_candidate("c1"), _candidate("c2", disabled=True)]
        cog = FakeCognitoDeleter()
        tomb = FakeTombstone()
        n = await run(lister=FakeLister(candidates), cognito_deleter=cog, tombstone=tomb)
        assert n == 2
        assert cog.calls == ["c1", "c2"]
        assert tomb.calls == ["c1", "c2"]


# --------------------------------------------------------------------------
# run() — dry-run
# --------------------------------------------------------------------------


class TestRunDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_lists_but_does_not_delete(self) -> None:
        candidates = [_candidate("c1")]
        cog = FakeCognitoDeleter()
        tomb = FakeTombstone()
        n = await run(
            lister=FakeLister(candidates),
            cognito_deleter=cog,
            tombstone=tomb,
            dry_run=True,
        )
        assert n == 0  # nothing deleted
        assert cog.calls == []
        assert tomb.calls == []


# --------------------------------------------------------------------------
# run() — error handling
# --------------------------------------------------------------------------


class TestRunErrorHandling:
    @pytest.mark.asyncio
    async def test_cognito_failure_skips_tombstone_and_continues(self) -> None:
        candidates = [_candidate("c1"), _candidate("c2"), _candidate("c3")]
        cog = FakeCognitoDeleter(fail_for={"c2"})
        tomb = FakeTombstone()
        n = await run(lister=FakeLister(candidates), cognito_deleter=cog, tombstone=tomb)
        # c1 + c3 succeed; c2 skipped
        assert n == 2
        assert cog.calls == ["c1", "c2", "c3"]
        assert tomb.calls == ["c1", "c3"]

    @pytest.mark.asyncio
    async def test_tombstone_failure_does_not_block_others(self) -> None:
        candidates = [_candidate("c1"), _candidate("c2")]
        cog = FakeCognitoDeleter()
        tomb = FakeTombstone(fail_for={"c1"})
        n = await run(lister=FakeLister(candidates), cognito_deleter=cog, tombstone=tomb)
        # c1: cognito ok, tombstone fail → not counted
        # c2: both ok → counted
        assert n == 1
        assert cog.calls == ["c1", "c2"]
        assert tomb.calls == ["c1", "c2"]


# --------------------------------------------------------------------------
# Runner CLI
# --------------------------------------------------------------------------


class TestRunnerCli:
    def test_runner_dispatches_known_job(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mem_mcp.jobs import _runner

        # Patch the cleanup_clients main to a no-op
        called: dict[str, bool] = {}

        async def fake_main(dry_run: bool = False) -> int:
            called["dry_run"] = dry_run
            return 7

        monkeypatch.setitem(_runner._JOBS, "cleanup_clients", fake_main)
        rc = _runner.main(["cleanup_clients", "--dry-run"])
        assert rc == 0
        assert called == {"dry_run": True}

    def test_runner_unknown_job_errors(self) -> None:
        from mem_mcp.jobs import _runner

        with pytest.raises(SystemExit):
            _runner.main(["nonexistent_job"])
