"""DB-backed tests for the memsys_admin_* tool family (admin-over-MCP, PR 1).

These live in tests/integration rather than tests/unit on purpose: the `unit`
CI job runs without MEM_MCP_TEST_DSN, so anything gated on `pg_pool` skips
there. The permission gate is the security control for this whole tool family,
so it has to run somewhere CI actually provides a database.

Fixtures create their own tenants with UUID-suffixed emails, so these do not
collide with the hardcoded-email fixtures elsewhere in the suite.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest

from mem_mcp.admin.errors import NotFoundError
from mem_mcp.admin.service import find_users, get_user
from mem_mcp.auth.permissions import Permission
from mem_mcp.db import system_tx, tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools.admin_find_user import AdminFindUserInput, AdminFindUserTool
from mem_mcp.mcp.tools.admin_get_user import AdminGetUserInput, AdminGetUserTool


async def _make_tenant(pool: Any, *, label: str) -> tuple[UUID, UUID, str]:
    """Create a tenant + primary identity with a collision-proof email."""
    tenant_id = uuid4()
    identity_id = uuid4()
    email = f"{label}-{tenant_id}@admin-it.invalid"
    async with system_tx(pool) as conn:
        await conn.execute(
            "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
            tenant_id,
            email,
        )
        await conn.execute(
            """
            INSERT INTO tenant_identities
                (id, tenant_id, cognito_sub, provider, email, is_primary)
            VALUES ($1, $2, $3, 'cognito', $4, true)
            """,
            identity_id,
            tenant_id,
            f"sub-{tenant_id}",
            email,
        )
    return tenant_id, identity_id, email


def _ctx(pool: Any, tenant_id: UUID, identity_id: UUID) -> ToolContext:
    return ToolContext(
        request_id=str(uuid4()),
        tenant_id=tenant_id,
        identity_id=identity_id,
        client_id="admin-it",
        scopes=frozenset({"memory.read", "memory.write"}),
        db_pool=pool,
    )


@pytest.fixture
async def plain_ctx(pg_pool: Any) -> AsyncIterator[ToolContext]:
    """A caller with no system role."""
    tenant_id, identity_id, _ = await _make_tenant(pg_pool, label="plain")
    yield _ctx(pg_pool, tenant_id, identity_id)


@pytest.fixture
async def admin_ctx(pg_pool: Any) -> AsyncIterator[ToolContext]:
    """A caller holding system_admin."""
    tenant_id, identity_id, _ = await _make_tenant(pg_pool, label="admin")
    async with system_tx(pg_pool) as conn:
        await conn.execute(
            """
            INSERT INTO tenant_system_roles (tenant_id, role)
            VALUES ($1, 'system_admin')
            ON CONFLICT DO NOTHING
            """,
            tenant_id,
        )
    yield _ctx(pg_pool, tenant_id, identity_id)


class TestPermissionGate:
    """AdminTool.__call__ must refuse before doing any work."""

    async def test_non_admin_denied_with_named_permission(self, plain_ctx: ToolContext) -> None:
        tool = AdminFindUserTool()
        with pytest.raises(JsonRpcError) as exc:
            await tool(plain_ctx, AdminFindUserInput(query="admin-"))
        assert exc.value.data is not None
        assert exc.value.data["missing_permission"] == Permission.SYSTEM_MANAGE_TENANTS.value

    async def test_denied_call_never_reaches_run(
        self, plain_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The regression guard for the whole design.

        If someone moves the check into run(), or a subclass overrides
        __call__, this fails rather than silently opening the tool up.
        """
        called = False

        async def _spy(*args: Any, **kwargs: Any) -> Any:
            nonlocal called
            called = True
            raise AssertionError("run() reached despite denied permission")

        monkeypatch.setattr(AdminFindUserTool, "run", _spy)

        tool = AdminFindUserTool()
        with pytest.raises(JsonRpcError):
            await tool(plain_ctx, AdminFindUserInput(query="admin-"))
        assert called is False

    async def test_system_admin_allowed(self, admin_ctx: ToolContext) -> None:
        """The same call succeeds once the caller holds system_admin."""
        tool = AdminFindUserTool()
        output = await tool(admin_ctx, AdminFindUserInput(query="admin-"))
        assert output.count >= 1  # type: ignore[attr-defined]


class TestFindUsers:
    async def test_matches_email_case_insensitively(
        self, admin_ctx: ToolContext, pg_pool: Any
    ) -> None:
        async with system_tx(pg_pool) as conn:
            rows = await find_users(conn, query="ADMIN-")
        assert any(r.tenant_id == str(admin_ctx.tenant_id) for r in rows)

    async def test_rejects_short_query(self, pg_pool: Any) -> None:
        async with system_tx(pg_pool) as conn:
            with pytest.raises(ValueError):
                await find_users(conn, query="a")

    async def test_limit_is_clamped(self, admin_ctx: ToolContext, pg_pool: Any) -> None:
        async with system_tx(pg_pool) as conn:
            rows = await find_users(conn, query="admin-it.invalid", limit=9999)
        assert len(rows) <= 100

    async def test_bare_wildcard_matches_nothing(
        self, admin_ctx: ToolContext, pg_pool: Any
    ) -> None:
        """A literal % must not become 'match every user on the instance'."""
        async with system_tx(pg_pool) as conn:
            rows = await find_users(conn, query="%%")
        assert rows == []

    @pytest.mark.parametrize(
        "probe",
        ["' OR '1'='1", "'; DROP TABLE tenants; --", "%' --", "\\\\"],
    )
    async def test_injection_probes_are_literal(self, pg_pool: Any, probe: str) -> None:
        async with system_tx(pg_pool) as conn:
            assert await find_users(conn, query=probe) == []
            # Table still present — nothing was executed.
            assert await conn.fetchval("SELECT COUNT(*) FROM tenants") >= 1


class TestGetUser:
    async def test_unknown_tenant_raises(self, pg_pool: Any) -> None:
        async with system_tx(pg_pool) as conn:
            with pytest.raises(NotFoundError):
                await get_user(conn, tenant_id=uuid4())

    async def test_resolves_system_roles(self, admin_ctx: ToolContext, pg_pool: Any) -> None:
        async with system_tx(pg_pool) as conn:
            detail = await get_user(conn, tenant_id=admin_ctx.tenant_id)
        assert "system_admin" in detail.system_roles

    async def test_tool_maps_missing_user_to_invalid_params(self, admin_ctx: ToolContext) -> None:
        tool = AdminGetUserTool()
        with pytest.raises(JsonRpcError) as exc:
            await tool(admin_ctx, AdminGetUserInput(tenant_id=uuid4()))
        assert exc.value.code == -32602


class TestNoExistenceOracle:
    """A denied caller must not learn whether a record exists."""

    async def test_denial_is_identical_for_real_and_fake_ids(self, plain_ctx: ToolContext) -> None:
        tool = AdminGetUserTool()

        with pytest.raises(JsonRpcError) as real:
            await tool(plain_ctx, AdminGetUserInput(tenant_id=plain_ctx.tenant_id))
        with pytest.raises(JsonRpcError) as fake:
            await tool(plain_ctx, AdminGetUserInput(tenant_id=uuid4()))

        assert real.value.code == fake.value.code
        assert real.value.data == fake.value.data


class TestGucRecycling:
    """Regression: system_tx on a connection recycled from a rolled-back tenant_tx.

    Postgres custom GUCs reset to '' rather than NULL after ROLLBACK, so a bare
    current_setting(...)::uuid crashes on a recycled pool connection. Admin
    tools run under system_tx on shared pool connections — exactly this path.
    """

    async def test_admin_read_survives_recycled_connection(
        self, admin_ctx: ToolContext, pg_pool: Any
    ) -> None:
        with pytest.raises(RuntimeError):
            async with tenant_tx(pg_pool, admin_ctx.tenant_id) as conn:
                await conn.fetchval("SELECT 1")
                raise RuntimeError("forced rollback")

        async with system_tx(pg_pool) as conn:
            assert isinstance(await find_users(conn, query="admin-"), list)
