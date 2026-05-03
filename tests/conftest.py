"""Shared test fixtures and configuration.

Per LLD §11 + T-6.2.

Live-DB fixtures (pg_pool, setup_two_tenants) skip cleanly when
MEM_MCP_TEST_DSN env var is not set. Set this env var to point at a
disposable Postgres (e.g. local docker, ephemeral RDS instance) before
running the security suite.

Mock fixtures (jwt_factory, mcp_client) work without DB.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest


@dataclass(frozen=True)
class FakeTenant:
    """A test tenant with ID, email, and JWT token."""

    tenant_id: UUID
    email: str
    token: str  # JWT (mocked or real depending on mode)


@dataclass(frozen=True)
class TenantPair:
    """Two test tenants for two-tenant test scenarios."""

    a: FakeTenant
    b: FakeTenant


def _mint_jwt(tenant_id: UUID) -> str:
    """Mint a fake JWT with a marker payload. For mock tests."""
    return f"fake.jwt.{tenant_id}"


@pytest.fixture
async def pg_pool() -> AsyncIterator[Any]:
    """Live Postgres connection pool (skips when MEM_MCP_TEST_DSN unset).

    Usage: use with tenant_tx() or system_tx() to run queries.
    """
    dsn = os.environ.get("MEM_MCP_TEST_DSN")
    if not dsn:
        pytest.skip("MEM_MCP_TEST_DSN env not set; skipping live-DB test")
    import asyncpg  # type: ignore[import-untyped]

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def setup_two_tenants(pg_pool: Any) -> TenantPair:
    """Insert two test tenants into the test DB. Returns TenantPair(a, b).

    Both tenants are marked as 'active' in the database.
    Call within a test that also uses pg_pool fixture.
    """
    a_id = uuid4()
    b_id = uuid4()
    a_email = f"a-{a_id}@test.invalid"
    b_email = f"b-{b_id}@test.invalid"
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active'), ($3, $4, 'active')",
            a_id,
            a_email,
            b_id,
            b_email,
        )
    return TenantPair(
        a=FakeTenant(tenant_id=a_id, email=a_email, token=_mint_jwt(a_id)),
        b=FakeTenant(tenant_id=b_id, email=b_email, token=_mint_jwt(b_id)),
    )


@pytest.fixture
def jwt_factory() -> Any:
    """Mocks a JWT factory. Returns callable(tenant_id) -> fake JWT string."""
    return _mint_jwt


@pytest.fixture
def mcp_client() -> Any:
    """Stub MCP client with write() and search() methods.

    For now: deterministic mock returning empty results. Live MCP client
    integration lands in a future PR.
    """

    class _StubMcpClient:
        async def write(self, token: str, content: str) -> dict[str, Any]:
            return {"id": str(uuid4()), "version": 1}

        async def search(self, token: str, query: str, **kwargs: Any) -> list[dict[str, Any]]:
            return []

    return _StubMcpClient()
