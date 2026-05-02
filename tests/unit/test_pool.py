"""Tests for mem_mcp.db.pool — accessor + lifecycle, no real DB."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from mem_mcp.db import pool as pool_module
from mem_mcp.db.pool import _reset_for_tests, get_pool


@pytest.fixture(autouse=True)
def _isolate_pool() -> Iterator[None]:
    _reset_for_tests()
    yield
    _reset_for_tests()


class TestGetPool:
    def test_raises_when_not_initialized(self) -> None:
        with pytest.raises(RuntimeError, match="not initialized"):
            get_pool()

    def test_returns_pool_after_manual_set(self) -> None:
        """Without a real DB we manually inject a sentinel via the module-private."""
        sentinel = object()
        pool_module._pool = sentinel
        try:
            assert get_pool() is sentinel
        finally:
            _reset_for_tests()
