"""Unit tests for notice template rendering."""

from __future__ import annotations

import logging

import pytest

from mem_mcp.plugins.notice_delivery import _render


class TestRender:
    def test_simple_substitution(self) -> None:
        assert _render("Hello {name}", {"name": "World"}) == "Hello World"

    def test_multiple_placeholders(self) -> None:
        out = _render("{a} and {b}", {"a": "x", "b": "y"})
        assert out == "x and y"

    def test_missing_key_renders_empty_and_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            out = _render("Hi {missing}", {})
        assert out == "Hi "
        assert any("notice_template_missing_key" in r.message for r in caplog.records)

    def test_non_string_payload_value_coerced(self) -> None:
        assert _render("count={n}", {"n": 42}) == "count=42"

    def test_no_placeholders(self) -> None:
        assert _render("plain text", {}) == "plain text"

    def test_payload_extra_keys_ignored(self) -> None:
        assert _render("{a}", {"a": "1", "b": "2"}) == "1"

    def test_brace_not_placeholder(self) -> None:
        # Only {ident} pattern matches; {1} or {a-b} aren't placeholders.
        assert _render("{1}", {}) == "{1}"
        assert _render("{a-b}", {}) == "{a-b}"

    def test_underscore_and_digits_in_key(self) -> None:
        assert _render("{user_id_2}", {"user_id_2": "42"}) == "42"
