"""Tests for mem_mcp.health helpers (T-3.5)."""

from __future__ import annotations

from mem_mcp.health import CheckResult, aggregate


class TestCheckResult:
    def test_is_ok(self) -> None:
        assert CheckResult("x", "ok").is_ok() is True
        assert CheckResult("x", "fail").is_ok() is False


class TestAggregate:
    def test_all_ok(self) -> None:
        status, payload = aggregate([CheckResult("a", "ok"), CheckResult("b", "ok")])
        assert status == "ok"
        assert payload == {"a": "ok", "b": "ok"}

    def test_one_failure(self) -> None:
        status, payload = aggregate([CheckResult("a", "ok"), CheckResult("b", "fail", "boom")])
        assert status == "fail"
        assert payload == {"a": "ok", "b": "boom"}

    def test_failure_without_message(self) -> None:
        status, payload = aggregate([CheckResult("a", "fail")])
        assert status == "fail"
        assert payload == {"a": "fail"}

    def test_empty(self) -> None:
        status, payload = aggregate([])
        assert status == "ok"
        assert payload == {}
