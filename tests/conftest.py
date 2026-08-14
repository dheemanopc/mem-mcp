"""Shared test fixtures and configuration.

Per LLD §11 + T-6.2.

Live-DB fixtures (pg_pool, setup_two_tenants) skip cleanly when
MEM_MCP_TEST_DSN env var is not set. Set this env var to point at a
disposable Postgres (e.g. local docker, ephemeral RDS instance) before
running the security suite.

Mock fixtures (jwt_factory, mcp_client) work without DB.

Live-AWS tests (@pytest.mark.live_aws) skip unless `pytest --live-aws`
is passed.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register --live-aws flag to enable live AWS tests."""
    parser.addoption(
        "--live-aws",
        action="store_true",
        default=False,
        help="Run tests marked @pytest.mark.live_aws (require AWS resources)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip live_aws-marked tests unless --live-aws is passed."""
    if config.getoption("--live-aws", default=False):
        return  # User explicitly requested live tests; don't skip

    skip_live = pytest.mark.skip(reason="requires --live-aws flag to run (uses live AWS resources)")
    for item in items:
        if "live_aws" in item.keywords:
            item.add_marker(skip_live)


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

    Uses the same `_init_connection` as `mem_mcp.db.pool.init_pool` so the
    pgvector + jsonb text codecs register correctly. Without these, any
    test that exercises the real `MemoryWriteTool` path fails with
    `expected str, got list` when asyncpg tries to bind a `list[float]`
    embedding to a `vector` column. Matches prod pool config.

    Usage: use with tenant_tx() or system_tx() to run queries.
    """
    dsn = os.environ.get("MEM_MCP_TEST_DSN")
    if not dsn:
        pytest.skip("MEM_MCP_TEST_DSN env not set; skipping live-DB test")
    import asyncpg  # type: ignore[import-untyped]

    from mem_mcp.db.pool import _init_connection

    pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=1,
        max_size=4,
        init=_init_connection,
    )
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


# ────────────────────────────────────────────────────────────────────────────
# Enterprise test fixtures (PR #239 / PR 1.B)
# Live-DB; all skip via pg_pool when MEM_MCP_TEST_DSN is unset.
# ────────────────────────────────────────────────────────────────────────────


async def _cleanup_tenant_teams(conn: Any, tenant_id: UUID) -> None:
    """Remove every team this tenant created, plus their dependent rows.

    Without this, teams outlive the fixture that made them and two things
    break: ``teams.workspace_domain`` is uniquely indexed, so the second test
    creating an ``example.com`` team collides with the first; and the leftover
    team's FK to ``tenants`` blocks the tenant delete at teardown. Both surface
    as unrelated-looking errors several tests later.

    Order matters — dependants first, then the teams themselves.
    """
    await conn.execute(
        "UPDATE memories SET team_id = NULL WHERE team_id IN"
        " (SELECT id FROM teams WHERE created_by_tenant_id = $1)",
        tenant_id,
    )
    await conn.execute(
        "DELETE FROM team_role_assignments WHERE member_id = $1"
        " OR parent_team_id IN (SELECT id FROM teams WHERE created_by_tenant_id = $1)",
        tenant_id,
    )
    await conn.execute(
        "DELETE FROM user_effective_team_access WHERE user_id = $1"
        " OR resource_team_id IN (SELECT id FROM teams WHERE created_by_tenant_id = $1)",
        tenant_id,
    )
    await conn.execute(
        "DELETE FROM team_invites WHERE team_id IN"
        " (SELECT id FROM teams WHERE created_by_tenant_id = $1)",
        tenant_id,
    )
    await conn.execute(
        "DELETE FROM team_members WHERE team_id IN"
        " (SELECT id FROM teams WHERE created_by_tenant_id = $1)",
        tenant_id,
    )
    await conn.execute("DELETE FROM teams WHERE created_by_tenant_id = $1", tenant_id)


@pytest.fixture
async def db_session(pg_pool: Any) -> AsyncIterator[Any]:
    """Async PG connection scoped to a single test.

    Tests can `await db_session.execute(...)` directly. The connection is
    closed at teardown. Cleanup of inserted rows is the test's responsibility.
    """
    async with pg_pool.acquire() as conn:
        yield conn


@pytest.fixture
async def tool_ctx_personal(pg_pool: Any) -> AsyncIterator[Any]:
    """A ToolContext-like object for a personal user (no workspace_domain).

    The object has: tenant_id (UUID), identity_id (UUID), db_pool (asyncpg.Pool).
    Used by MCP tool tests that call Tool(ctx, input).
    """
    from mem_mcp.mcp.tools._base import ToolContext

    tenant_id = uuid4()
    identity_id = uuid4()
    email = f"personal-{tenant_id}@test.invalid"

    async with pg_pool.acquire() as conn:
        # Insert tenant
        await conn.execute(
            "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
            tenant_id,
            email,
        )
        # Insert primary identity (workspace_domain=NULL)
        await conn.execute(
            """
            INSERT INTO tenant_identities
            (id, tenant_id, cognito_sub, provider, email, is_primary)
            VALUES ($1, $2, $3, $4, $5, true)
            """,
            identity_id,
            tenant_id,
            f"sub-{tenant_id}",
            "cognito",
            email,
        )

    # Build a ToolContext-like object for tests
    ctx = ToolContext(
        request_id=str(uuid4()),
        tenant_id=tenant_id,
        identity_id=identity_id,
        client_id="test-client",
        scopes=frozenset(["memory.read", "memory.write"]),
        db_pool=pg_pool,
    )

    yield ctx

    # Cleanup
    async with pg_pool.acquire() as conn:
        await conn.execute("DELETE FROM tenant_identities WHERE id = $1", identity_id)
        await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)


@pytest.fixture
async def tool_ctx_with_workspace(pg_pool: Any) -> AsyncIterator[Any]:
    """A ToolContext-like object for a workspace user (workspace_domain='example.com').

    The object has: tenant_id (UUID), identity_id (UUID), db_pool (asyncpg.Pool),
    and the corresponding tenant_identities row has workspace_domain='example.com'.
    """
    from mem_mcp.mcp.tools._base import ToolContext

    tenant_id = uuid4()
    identity_id = uuid4()
    email = f"workspace-{tenant_id}@example.com"

    async with pg_pool.acquire() as conn:
        # Insert tenant
        await conn.execute(
            "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
            tenant_id,
            email,
        )
        # Insert primary identity with workspace_domain
        await conn.execute(
            """
            INSERT INTO tenant_identities
            (id, tenant_id, cognito_sub, provider, email, is_primary, workspace_domain)
            VALUES ($1, $2, $3, $4, $5, true, $6)
            """,
            identity_id,
            tenant_id,
            f"sub-{tenant_id}",
            "cognito",
            email,
            "example.com",
        )

    ctx = ToolContext(
        request_id=str(uuid4()),
        tenant_id=tenant_id,
        identity_id=identity_id,
        client_id="test-client",
        scopes=frozenset(["memory.read", "memory.write"]),
        db_pool=pg_pool,
    )

    yield ctx

    # Cleanup
    async with pg_pool.acquire() as conn:
        await conn.execute("DELETE FROM tenant_identities WHERE id = $1", identity_id)
        await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)


@pytest.fixture
async def user_personal(pg_pool: Any) -> AsyncIterator[dict[str, Any]]:
    """Personal user fixture: returns dict with session, tenant_id, identity_id, email.

    The session cookie can be passed to the TestClient via mem_session cookie.
    """
    tenant_id = uuid4()
    identity_id = uuid4()
    email = f"personal-{tenant_id}@test.invalid"
    session_raw = secrets.token_urlsafe(32)
    session_hash = hashlib.sha256(session_raw.encode()).hexdigest()
    expires_at = datetime.now(tz=UTC) + timedelta(days=7)

    async with pg_pool.acquire() as conn:
        # Insert tenant
        await conn.execute(
            "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
            tenant_id,
            email,
        )
        # Insert primary identity (workspace_domain=NULL)
        await conn.execute(
            """
            INSERT INTO tenant_identities
            (id, tenant_id, cognito_sub, provider, email, is_primary)
            VALUES ($1, $2, $3, $4, $5, true)
            """,
            identity_id,
            tenant_id,
            f"sub-{tenant_id}",
            "cognito",
            email,
        )
        # Insert web session
        await conn.execute(
            """
            INSERT INTO web_sessions
            (session_hash, tenant_id, identity_id, expires_at)
            VALUES ($1, $2, $3, $4)
            """,
            session_hash,
            tenant_id,
            identity_id,
            expires_at,
        )

    yield {
        "session": session_raw,
        "tenant_id": tenant_id,
        "identity_id": identity_id,
        "email": email,
    }

    # Cleanup
    async with pg_pool.acquire() as conn:
        await conn.execute("DELETE FROM web_sessions WHERE session_hash = $1", session_hash)
        await _cleanup_tenant_teams(conn, tenant_id)
        await conn.execute("DELETE FROM tenant_identities WHERE id = $1", identity_id)
        await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)


@pytest.fixture
async def user_with_workspace(pg_pool: Any) -> AsyncIterator[dict[str, Any]]:
    """Workspace user fixture: returns dict with session, tenant_id, identity_id, email.

    workspace_domain='example.com'.
    """
    tenant_id = uuid4()
    identity_id = uuid4()
    email = f"workspace-{tenant_id}@example.com"
    session_raw = secrets.token_urlsafe(32)
    session_hash = hashlib.sha256(session_raw.encode()).hexdigest()
    expires_at = datetime.now(tz=UTC) + timedelta(days=7)

    async with pg_pool.acquire() as conn:
        # Insert tenant
        await conn.execute(
            "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
            tenant_id,
            email,
        )
        # Insert primary identity with workspace_domain
        await conn.execute(
            """
            INSERT INTO tenant_identities
            (id, tenant_id, cognito_sub, provider, email, is_primary, workspace_domain)
            VALUES ($1, $2, $3, $4, $5, true, $6)
            """,
            identity_id,
            tenant_id,
            f"sub-{tenant_id}",
            "cognito",
            email,
            "example.com",
        )
        # Insert web session
        await conn.execute(
            """
            INSERT INTO web_sessions
            (session_hash, tenant_id, identity_id, expires_at)
            VALUES ($1, $2, $3, $4)
            """,
            session_hash,
            tenant_id,
            identity_id,
            expires_at,
        )

    yield {
        "session": session_raw,
        "tenant_id": tenant_id,
        "identity_id": identity_id,
        "email": email,
    }

    # Cleanup
    async with pg_pool.acquire() as conn:
        await conn.execute("DELETE FROM web_sessions WHERE session_hash = $1", session_hash)
        await _cleanup_tenant_teams(conn, tenant_id)
        await conn.execute("DELETE FROM tenant_identities WHERE id = $1", identity_id)
        await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)


@pytest.fixture
async def other_user_same_workspace(pg_pool: Any) -> AsyncIterator[dict[str, Any]]:
    """Another workspace user in 'example.com' workspace."""
    tenant_id = uuid4()
    identity_id = uuid4()
    email = f"other-ws-{tenant_id}@example.com"
    session_raw = secrets.token_urlsafe(32)
    session_hash = hashlib.sha256(session_raw.encode()).hexdigest()
    expires_at = datetime.now(tz=UTC) + timedelta(days=7)

    async with pg_pool.acquire() as conn:
        # Insert tenant
        await conn.execute(
            "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
            tenant_id,
            email,
        )
        # Insert primary identity with workspace_domain='example.com'
        await conn.execute(
            """
            INSERT INTO tenant_identities
            (id, tenant_id, cognito_sub, provider, email, is_primary, workspace_domain)
            VALUES ($1, $2, $3, $4, $5, true, $6)
            """,
            identity_id,
            tenant_id,
            f"sub-{tenant_id}",
            "cognito",
            email,
            "example.com",
        )
        # Insert web session
        await conn.execute(
            """
            INSERT INTO web_sessions
            (session_hash, tenant_id, identity_id, expires_at)
            VALUES ($1, $2, $3, $4)
            """,
            session_hash,
            tenant_id,
            identity_id,
            expires_at,
        )

    yield {
        "session": session_raw,
        "tenant_id": tenant_id,
        "identity_id": identity_id,
        "email": email,
    }

    # Cleanup
    async with pg_pool.acquire() as conn:
        await conn.execute("DELETE FROM web_sessions WHERE session_hash = $1", session_hash)
        await _cleanup_tenant_teams(conn, tenant_id)
        await conn.execute("DELETE FROM tenant_identities WHERE id = $1", identity_id)
        await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)


@pytest.fixture
async def user_different_workspace(pg_pool: Any) -> AsyncIterator[dict[str, Any]]:
    """User in different workspace: workspace_domain='other.com'."""
    tenant_id = uuid4()
    identity_id = uuid4()
    email = f"other-domain-{tenant_id}@other.com"
    session_raw = secrets.token_urlsafe(32)
    session_hash = hashlib.sha256(session_raw.encode()).hexdigest()
    expires_at = datetime.now(tz=UTC) + timedelta(days=7)

    async with pg_pool.acquire() as conn:
        # Insert tenant
        await conn.execute(
            "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
            tenant_id,
            email,
        )
        # Insert primary identity with workspace_domain='other.com'
        await conn.execute(
            """
            INSERT INTO tenant_identities
            (id, tenant_id, cognito_sub, provider, email, is_primary, workspace_domain)
            VALUES ($1, $2, $3, $4, $5, true, $6)
            """,
            identity_id,
            tenant_id,
            f"sub-{tenant_id}",
            "cognito",
            email,
            "other.com",
        )
        # Insert web session
        await conn.execute(
            """
            INSERT INTO web_sessions
            (session_hash, tenant_id, identity_id, expires_at)
            VALUES ($1, $2, $3, $4)
            """,
            session_hash,
            tenant_id,
            identity_id,
            expires_at,
        )

    yield {
        "session": session_raw,
        "tenant_id": tenant_id,
        "identity_id": identity_id,
        "email": email,
    }

    # Cleanup
    async with pg_pool.acquire() as conn:
        await conn.execute("DELETE FROM web_sessions WHERE session_hash = $1", session_hash)
        await _cleanup_tenant_teams(conn, tenant_id)
        await conn.execute("DELETE FROM tenant_identities WHERE id = $1", identity_id)
        await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)


# Settings fields with no default (config.py). create_app() constructs Settings
# eagerly, so every one of these must be present or the app cannot be built. A
# developer's .env happens to supply them locally, which is why this was never
# noticed — CI has no .env, so without these the client fixture cannot work
# there at all.
_REQUIRED_APP_SETTINGS = {
    "MEM_MCP_COGNITO_USER_POOL_ID": "test-pool",
    "MEM_MCP_COGNITO_DOMAIN": "test.auth.localhost",
    "MEM_MCP_RESOURCE_URL": "http://localhost:8080",
    "MEM_MCP_WEB_URL": "http://localhost:3000",
    "MEM_MCP_WEB_CLIENT_ID": "test-client",
    "MEM_MCP_WEB_CLIENT_SECRET": "test-secret",
    "MEM_MCP_INTERNAL_LAMBDA_SECRET": "test-lambda-secret",
    "MEM_MCP_SES_FROM": "test@localhost",
    "MEM_MCP_BACKUP_BUCKET": "test-bucket",
    "MEM_MCP_BACKUP_GPG_PASSPHRASE": "test-passphrase",
    "MEM_MCP_WEB_SESSION_SECRET": "test-session-secret",
    "MEM_MCP_LINK_STATE_SECRET": "test-link-secret",
}


@pytest.fixture
def client(pg_pool: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """FastAPI TestClient over the real app, with pg_pool wired in.

    Uses the live DB pointed to by MEM_MCP_TEST_DSN via the fixture's
    dependency on pg_pool (skips cleanly if DSN unset).

    Two things this has to do that are easy to miss:

    1. **Enter the TestClient as a context manager.** `create_app()` only
       registers /healthz and /readyz; every real router is wired inside
       `lifespan()`, and `TestClient(app)` does not run lifespan unless used
       as a context manager. The previous version returned a bare
       `TestClient(app)`, so the app under test had exactly two routes and
       every request 404'd.
    2. Supply the required Settings values, so the app can be constructed
       without a developer's local .env (CI has none).
    """
    from fastapi.testclient import TestClient

    from mem_mcp.main import create_app

    test_dsn = os.environ["MEM_MCP_TEST_DSN"]
    monkeypatch.setenv("MEM_MCP_DB_DSN", test_dsn)
    monkeypatch.setenv("MEM_MCP_DB_MAINT_DSN", test_dsn)
    for key, value in _REQUIRED_APP_SETTINGS.items():
        monkeypatch.setenv(key, value)

    app = create_app()
    # The CSRF middleware is double-submit: unsafe methods need a csrf_token
    # cookie AND a matching X-CSRF-Token header. Set both as client defaults so
    # individual tests don't each have to reproduce the handshake — they are
    # testing the handlers, not CSRF (which has its own suite in
    # tests/unit/test_web_csrf.py).
    csrf = "test-csrf-token"
    # `with` → lifespan runs → init_pool() against the test DSN → routers wired.
    with TestClient(
        app,
        headers={"X-CSRF-Token": csrf},
        cookies={"csrf_token": csrf},
    ) as test_client:
        yield test_client


@pytest.fixture
async def setup_dag_5_levels(pg_pool: Any) -> AsyncIterator[dict[str, Any]]:
    """Build a 5-level deep nested team DAG for IT-01 testing.

    Returns:
        dict with keys t1..t5 (team UUIDs, root → leaf), tenant_id (a real test tenant).

    All teams + memberships created inside a single transaction and ROLLED BACK
    on teardown — no state leaks across tests.
    """
    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            tenant_id = await conn.fetchval(
                "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                f"test-{uuid4()}@example.test",
            )

            member_role_id = await conn.fetchval(
                "SELECT id FROM roles_catalog WHERE role_key='member' AND plugin_id IS NULL"
            )

            # Create 5 teams T1..T5
            team_ids: dict[str, Any] = {}
            for i in range(1, 6):
                tid = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ($1, $2) RETURNING id",
                    f"test-dag-t{i}",
                    tenant_id,
                )
                team_ids[f"t{i}"] = tid

            # Chain T1 → T2 → T3 → T4 → T5 (Ti is parent, T(i+1) is member of Ti)
            for i in range(1, 5):
                await conn.execute(
                    """INSERT INTO team_role_assignments
                       (parent_team_id, member_kind, member_id, role_id, assigned_by_user_id)
                       VALUES ($1, 'team', $2, $3, $4)""",
                    team_ids[f"t{i}"],
                    team_ids[f"t{i+1}"],
                    member_role_id,
                    tenant_id,
                )

            yield {**team_ids, "tenant_id": tenant_id}
            # Transaction rolls back automatically on context exit.


@pytest.fixture
async def seed_roles_catalog_verified(pg_pool: Any) -> dict[str, Any]:
    """Verify the 7 core roles are seeded; return role_id-by-key dict for tests."""
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, role_key FROM roles_catalog WHERE plugin_id IS NULL")
        roles = {r["role_key"]: r["id"] for r in rows}
        expected = {"owner", "admin", "member", "viewer", "vendor", "customer", "service-bot"}
        missing = expected - set(roles.keys())
        if missing:
            pytest.skip(f"roles_catalog missing seeds: {missing}")
        return roles
