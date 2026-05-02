"""Tests for mem_mcp.logging_setup (T-3.3)."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator

import pytest

from mem_mcp.logging_setup import (
    _is_sensitive_key,
    _redact,
    _redact_processor,
    _reset_for_tests,
    bind_request_context,
    clear_request_context,
    get_logger,
    setup_logging,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_logging() -> Iterator[None]:
    """Reset structlog state before AND after every test."""
    _reset_for_tests()
    yield
    _reset_for_tests()


# ---------------------------------------------------------------------------
# _is_sensitive_key
# ---------------------------------------------------------------------------


class TestIsSensitiveKey:
    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "Password",
            "DB_PASSWORD",
            "client_secret",
            "Authorization",
            "x-amz-security-token",
            "mem_session",
            "refresh_token",
            "id_token",
            "access_token",
            "gpg_passphrase",
            "private_key",
        ],
    )
    def test_sensitive(self, key: str) -> None:
        assert _is_sensitive_key(key)

    @pytest.mark.parametrize(
        "key",
        [
            "user_id",
            "tenant_id",
            "request_id",
            "action",
            "latency_ms",
            "result",
            "method",
            "url",
            "content_length",
            "content_hash",
        ],
    )
    def test_not_sensitive(self, key: str) -> None:
        assert not _is_sensitive_key(key)


# ---------------------------------------------------------------------------
# _redact
# ---------------------------------------------------------------------------


class TestRedact:
    def test_top_level_key_redacted(self) -> None:
        result = _redact({"password": "hunter2", "user_id": "u1"})
        assert result == {"password": "<redacted>", "user_id": "u1"}

    def test_nested_dict_redacted(self) -> None:
        result = _redact({"outer": {"client_secret": "x"}, "user_id": "u1"})
        assert result == {"outer": {"client_secret": "<redacted>"}, "user_id": "u1"}

    def test_list_of_dicts(self) -> None:
        result = _redact([{"token": "abc"}, {"name": "foo"}])
        assert result == [{"token": "<redacted>"}, {"name": "foo"}]

    def test_non_mapping_returned_asis(self) -> None:
        assert _redact(42) == 42
        assert _redact("plain") == "plain"
        assert _redact(None) is None


# ---------------------------------------------------------------------------
# _redact_processor (structlog wiring)
# ---------------------------------------------------------------------------


class TestRedactProcessor:
    def test_top_level_redacted(self) -> None:
        ev = {"event": "auth", "password": "hunter2", "tenant_id": "t1"}
        out = _redact_processor(None, "info", ev)
        assert out["password"] == "<redacted>"
        assert out["tenant_id"] == "t1"
        assert out["event"] == "auth"

    def test_nested_redacted(self) -> None:
        ev = {"event": "x", "request": {"headers": {"Authorization": "Bearer abc"}}}
        out = _redact_processor(None, "info", ev)
        assert out["request"]["headers"]["Authorization"] == "<redacted>"


# ---------------------------------------------------------------------------
# setup_logging + end-to-end JSON output
# ---------------------------------------------------------------------------


class TestSetupLogging:
    def test_emits_json_with_required_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        buf = io.StringIO()
        import sys

        monkeypatch.setattr(sys, "stdout", buf)

        setup_logging("INFO")
        log = get_logger()
        log.info("test_event", action="memory.write", tenant_id="t1", latency_ms=42)

        output = buf.getvalue().strip()
        assert output, "no output captured"
        # The output should be valid JSON
        record = json.loads(output)
        assert record["event"] == "test_event"
        assert record["action"] == "memory.write"
        assert record["tenant_id"] == "t1"
        assert record["latency_ms"] == 42
        assert record["level"] == "info"
        assert "timestamp" in record
        assert record["timestamp"].endswith("Z") or "+" in record["timestamp"]  # ISO UTC

    def test_secrets_redacted_in_emitted_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        buf = io.StringIO()
        import sys

        monkeypatch.setattr(sys, "stdout", buf)

        setup_logging("INFO")
        log = get_logger()
        log.info(
            "auth_attempt",
            user_id="u1",
            password="hunter2",
            client_secret="abcd",
            access_token="xyz",
        )

        record = json.loads(buf.getvalue().strip())
        assert record["user_id"] == "u1"
        assert record["password"] == "<redacted>"
        assert record["client_secret"] == "<redacted>"
        assert record["access_token"] == "<redacted>"
        # Original values must not leak ANYWHERE in the rendered string
        raw = buf.getvalue()
        assert "hunter2" not in raw
        assert "abcd" not in raw

    def test_setup_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        buf = io.StringIO()
        import sys

        monkeypatch.setattr(sys, "stdout", buf)

        setup_logging("INFO")
        setup_logging("DEBUG")  # second call: no-op (returns early)
        log = get_logger()
        log.info("hello")
        record = json.loads(buf.getvalue().strip())
        # Level on the EVENT is INFO regardless; the wrapper class was set at INFO threshold.
        # Just verify setup didn't crash and log went through.
        assert record["event"] == "hello"

    def test_request_context_binds_and_clears(self, monkeypatch: pytest.MonkeyPatch) -> None:
        buf = io.StringIO()
        import sys

        monkeypatch.setattr(sys, "stdout", buf)

        setup_logging("INFO")
        log = get_logger()

        bind_request_context(request_id="req-abc", tenant_id="t1")
        log.info("first")
        clear_request_context()
        log.info("second")

        lines = [json.loads(line) for line in buf.getvalue().strip().split("\n") if line]
        assert len(lines) == 2
        assert lines[0]["request_id"] == "req-abc"
        assert lines[0]["tenant_id"] == "t1"
        assert "request_id" not in lines[1]
        assert "tenant_id" not in lines[1]
