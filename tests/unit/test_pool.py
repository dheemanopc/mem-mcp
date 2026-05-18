"""Tests for mem_mcp.db.pool — accessor + lifecycle, no real DB."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest

from mem_mcp.db import pool as pool_module
from mem_mcp.db.pool import _init_connection, _reset_for_tests, get_pool


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


class TestInitConnectionCodecs:
    """Codecs registered on each new asyncpg connection.

    Regression test for bug fed1023c: memory_get / memory_thread_get return
    -32603 internal error because JSONB columns ship as str (no codec),
    failing Pydantic dict validation. The init function MUST register both
    jsonb and json codecs.
    """

    @pytest.mark.asyncio
    async def test_registers_vector_and_jsonb_codecs(self) -> None:
        conn = AsyncMock()
        await _init_connection(conn)

        # Should have called set_type_codec three times: vector, jsonb, json
        assert conn.set_type_codec.call_count == 3
        registered_types = {c.args[0] for c in conn.set_type_codec.call_args_list}
        assert registered_types == {"vector", "jsonb", "json"}

    @pytest.mark.asyncio
    async def test_jsonb_codec_uses_json_loads_for_decode(self) -> None:
        """The jsonb decoder must parse JSON strings to dict (else Pydantic models break)."""
        import json as json_mod

        conn = AsyncMock()
        await _init_connection(conn)

        jsonb_call = next(c for c in conn.set_type_codec.call_args_list if c.args[0] == "jsonb")
        # decoder kwarg should be json.loads (parses str → dict)
        assert jsonb_call.kwargs["decoder"] is json_mod.loads
        # encoder must accept dict and return str (json.dumps)
        assert jsonb_call.kwargs["encoder"] is json_mod.dumps
        # Scoped to pg_catalog (asyncpg requirement for built-in types)
        assert jsonb_call.kwargs["schema"] == "pg_catalog"
