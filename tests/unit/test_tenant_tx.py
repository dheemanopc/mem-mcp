"""Tests for mem_mcp.db.tenant_tx (T-3.4).

No real Postgres required — uses a fake asyncpg-shaped pool that records calls.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest

from mem_mcp.db.tenant_tx import system_tx, tenant_tx


# ---------------------------------------------------------------------------
# Fake asyncpg primitives
# ---------------------------------------------------------------------------


class FakeConnection:
    """Records every execute/fetch call and tracks transaction nesting."""

    def __init__(self, conn_id: int) -> None:
        self.conn_id = conn_id
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.transaction_depth = 0
        self.released = False

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "OK"

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        self.transaction_depth += 1
        try:
            yield
        finally:
            self.transaction_depth -= 1


class FakePool:
    """Hands out FakeConnection instances and tracks acquire/release lifecycle."""

    def __init__(self) -> None:
        self.acquired: list[FakeConnection] = []
        self._next_id = 0

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[FakeConnection]:
        self._next_id += 1
        conn = FakeConnection(self._next_id)
        self.acquired.append(conn)
        try:
            yield conn
        finally:
            conn.released = True


# ---------------------------------------------------------------------------
# tenant_tx
# ---------------------------------------------------------------------------


class TestTenantTx:
    @pytest.mark.asyncio
    async def test_sets_local_tenant_context(self) -> None:
        pool = FakePool()
        tid = uuid4()
        async with tenant_tx(pool, tid) as conn:
            assert conn.transaction_depth == 1
            assert conn.executed == [
                (
                    "SELECT set_config('app.current_tenant_id', $1, true)",
                    (str(tid),),
                )
            ]
        # After exit
        assert conn.released
        assert conn.transaction_depth == 0

    @pytest.mark.asyncio
    async def test_releases_connection_on_exception(self) -> None:
        pool = FakePool()
        tid = uuid4()
        with pytest.raises(RuntimeError, match="boom"):
            async with tenant_tx(pool, tid) as conn:
                raise RuntimeError("boom")
        assert conn.released

    @pytest.mark.asyncio
    async def test_uses_local_scope_argument(self) -> None:
        """The third positional arg to set_config MUST be True (= LOCAL).
        Without it, the setting persists across pool acquisitions and leaks
        between tenants. This is a non-negotiable invariant.
        """
        pool = FakePool()
        tid = uuid4()
        async with tenant_tx(pool, tid):
            pass
        conn = pool.acquired[0]
        query, args = conn.executed[0]
        assert "set_config" in query
        assert "true" in query.lower()  # third arg literally 'true' in the SQL
        assert args == (str(tid),)

    @pytest.mark.asyncio
    async def test_concurrent_tenants_get_separate_contexts(self) -> None:
        """Two tenant_tx blocks running concurrently each see their own tenant_id.

        Per spec §18.3 S-4: the per-process pool MUST not leak SET LOCAL
        across acquisitions. Modeled here by each FakeConnection being
        independent — verifies the *contract* (separate connections,
        separate set_config calls), not the asyncpg implementation.
        """
        pool = FakePool()
        results: list[UUID] = []

        async def worker(tid: UUID) -> None:
            async with tenant_tx(pool, tid) as conn:
                # Capture which tenant_id this connection saw
                _, args = conn.executed[0]
                results.append(UUID(args[0]))

        tids = [uuid4() for _ in range(20)]
        await asyncio.gather(*(worker(t) for t in tids))

        assert sorted(results) == sorted(tids)
        # Every acquisition got its own connection
        assert len({c.conn_id for c in pool.acquired}) == 20
        # All released
        assert all(c.released for c in pool.acquired)

    @pytest.mark.asyncio
    async def test_yields_the_acquired_connection(self) -> None:
        pool = FakePool()
        async with tenant_tx(pool, uuid4()) as conn:
            assert conn is pool.acquired[0]


# ---------------------------------------------------------------------------
# system_tx
# ---------------------------------------------------------------------------


class TestSystemTx:
    @pytest.mark.asyncio
    async def test_does_not_set_tenant_context(self) -> None:
        pool = FakePool()
        async with system_tx(pool) as conn:
            assert conn.transaction_depth == 1
            assert conn.executed == []  # NO set_config call
        assert conn.released

    @pytest.mark.asyncio
    async def test_releases_on_exception(self) -> None:
        pool = FakePool()
        with pytest.raises(ValueError):
            async with system_tx(pool) as conn:
                raise ValueError("test")
        assert conn.released

    @pytest.mark.asyncio
    async def test_yields_connection_inside_transaction(self) -> None:
        pool = FakePool()
        async with system_tx(pool) as conn:
            assert conn is pool.acquired[0]
            assert conn.transaction_depth == 1
