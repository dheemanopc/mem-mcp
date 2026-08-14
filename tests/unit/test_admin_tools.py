"""Tests for the memsys_admin_* tool family (admin-over-MCP, PR 1).

The gate tests matter more than the happy paths here: a missing permission
check on an admin tool is privilege escalation, so several of these assert on
the *absence* of behaviour (run() never reached, no rows returned) rather than
on a returned value.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from mem_mcp.admin.errors import NotFoundError
from mem_mcp.admin.service import _escape_like, find_users, get_user
from mem_mcp.auth.permissions import Permission
from mem_mcp.db import system_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools.admin_find_user import AdminFindUserInput, AdminFindUserTool
from mem_mcp.mcp.tools.admin_get_user import AdminGetUserInput, AdminGetUserTool


async def _grant_system_admin(pool: Any, tenant_id: Any) -> None:
    async with system_tx(pool) as conn:
        await conn.execute(
            """
            INSERT INTO tenant_system_roles (tenant_id, role)
            VALUES ($1, 'system_admin')
            ON CONFLICT (tenant_id, role) DO NOTHING
            """,
            tenant_id,
        )


class TestPermissionGate:
    """AdminTool.__call__ must refuse before doing any work."""

    async def test_non_admin_is_denied(self, tool_ctx_personal: Any) -> None:
        """A plain user gets -32603 naming the missing permission."""
        tool = AdminFindUserTool()
        inp = AdminFindUserInput(query="anything")

        with pytest.raises(JsonRpcError) as exc:
            await tool(tool_ctx_personal, inp)

        assert exc.value.data is not None
        assert exc.value.data["missing_permission"] == Permission.SYSTEM_MANAGE_TENANTS.value

    async def test_denied_call_never_reaches_run(
        self, tool_ctx_personal: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The gate short-circuits: run() must not execute for a denied caller.

        This is the regression guard for the whole design — if someone moves
        the check into run(), or a subclass overrides __call__, this fails.
        """
        called = False

        async def _spy(*args: Any, **kwargs: Any) -> Any:
            nonlocal called
            called = True
            raise AssertionError("run() reached despite denied permission")

        monkeypatch.setattr(AdminFindUserTool, "run", _spy)

        tool = AdminFindUserTool()
        with pytest.raises(JsonRpcError):
            await tool(tool_ctx_personal, AdminFindUserInput(query="anything"))

        assert called is False

    async def test_system_admin_is_allowed(self, tool_ctx_personal: Any, pg_pool: Any) -> None:
        """Granting system_admin flips the same call to success."""
        await _grant_system_admin(pg_pool, tool_ctx_personal.tenant_id)

        tool = AdminFindUserTool()
        output = await tool(tool_ctx_personal, AdminFindUserInput(query="personal-"))

        assert output.count >= 1  # type: ignore[attr-defined]

    async def test_no_admin_tool_overrides_call(self) -> None:
        """No admin tool may define its own __call__ — that would skip the gate."""
        from mem_mcp.mcp.tools._admin_base import AdminTool

        for tool_cls in (AdminFindUserTool, AdminGetUserTool):
            assert (
                "__call__" not in tool_cls.__dict__
            ), f"{tool_cls.__name__} overrides __call__ and bypasses the permission gate"
            assert tool_cls.required_permission is not None
            assert issubclass(tool_cls, AdminTool)


class TestFindUsers:
    """Service-layer search behaviour."""

    async def test_matches_email_case_insensitively(
        self, tool_ctx_personal: Any, pg_pool: Any
    ) -> None:
        async with system_tx(pg_pool) as conn:
            rows = await find_users(conn, query="PERSONAL-")
        assert any(r.tenant_id == str(tool_ctx_personal.tenant_id) for r in rows)

    async def test_rejects_short_query(self, pg_pool: Any) -> None:
        async with system_tx(pg_pool) as conn:
            with pytest.raises(ValueError):
                await find_users(conn, query="a")

    async def test_limit_is_clamped(self, tool_ctx_personal: Any, pg_pool: Any) -> None:
        """limit above the ceiling must not return more than the ceiling."""
        async with system_tx(pg_pool) as conn:
            rows = await find_users(conn, query="@test", limit=9999)
        assert len(rows) <= 100

    async def test_wildcards_are_escaped(self, tool_ctx_personal: Any, pg_pool: Any) -> None:
        """A bare % must be a literal, not 'match everything'.

        Without ESCAPE handling this returns the entire user table, which is
        both an information leak and a context-window hazard.
        """
        async with system_tx(pg_pool) as conn:
            rows = await find_users(conn, query="%%")
        assert rows == []

    def test_escape_like_handles_backslash_first(self) -> None:
        """Backslash must be escaped before the wildcards we add, or it doubles up."""
        assert _escape_like(r"100%") == r"100\%"
        assert _escape_like("a_b") == r"a\_b"
        assert _escape_like("\\") == "\\\\"


class TestGetUser:
    """Service-layer single-user fetch."""

    async def test_unknown_tenant_raises(self, pg_pool: Any) -> None:
        async with system_tx(pg_pool) as conn:
            with pytest.raises(NotFoundError):
                await get_user(conn, tenant_id=uuid4())

    async def test_resolves_system_roles(self, tool_ctx_personal: Any, pg_pool: Any) -> None:
        await _grant_system_admin(pg_pool, tool_ctx_personal.tenant_id)
        async with system_tx(pg_pool) as conn:
            detail = await get_user(conn, tenant_id=tool_ctx_personal.tenant_id)
        assert "system_admin" in detail.system_roles

    async def test_tool_maps_missing_user_to_invalid_params(
        self, tool_ctx_personal: Any, pg_pool: Any
    ) -> None:
        await _grant_system_admin(pg_pool, tool_ctx_personal.tenant_id)
        tool = AdminGetUserTool()
        with pytest.raises(JsonRpcError) as exc:
            await tool(tool_ctx_personal, AdminGetUserInput(tenant_id=uuid4()))
        assert exc.value.code == -32602


class TestGucRecycling:
    """Regression: system_tx on a connection recycled from a rolled-back tenant_tx.

    Postgres custom GUCs reset to '' rather than NULL after ROLLBACK, so a
    bare current_setting(...)::uuid crashes on a recycled pool connection.
    Admin tools run under system_tx on shared pool connections, which is
    exactly the path that trips this.
    """

    async def test_admin_read_survives_recycled_connection(
        self, tool_ctx_personal: Any, pg_pool: Any
    ) -> None:
        from mem_mcp.db import tenant_tx

        with pytest.raises(RuntimeError):
            async with tenant_tx(pg_pool, tool_ctx_personal.tenant_id) as conn:
                await conn.fetchval("SELECT 1")
                raise RuntimeError("forced rollback")

        # Same pool, likely the same physical connection.
        async with system_tx(pg_pool) as conn:
            rows = await find_users(conn, query="personal-")
        assert isinstance(rows, list)
