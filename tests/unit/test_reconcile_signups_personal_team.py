"""``_provision_one`` must give every new tenant a usable personal team.

Regression guard for 2026-08-11: the prod signup path created ``tenants`` and
``tenant_identities`` but no personal team, no owner role, and no
``default_team_id``. Signup succeeded; MCP login then failed for every such
user. Two beta users hit it before anyone noticed, because nothing in the
signup flow surfaces the omission — the account looks fine until you try to use
it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from mem_mcp.jobs.reconcile_signups import CognitoUserSummary, _provision_one

TENANT_ID = UUID("3cfb9a05-65d2-4073-872f-9b75008f5736")
TEAM_ID = UUID("11111111-2222-3333-4444-555555555555")


class _FakeConn:
    """Records every execute/fetchval/fetchrow so the SQL shape can be asserted."""

    def __init__(self, *, existing_personal_team: bool = False) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self._existing_personal_team = existing_personal_team

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.executed.append((sql, args))
        if "INSERT INTO tenants" in sql:
            return {"id": TENANT_ID}
        return None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.executed.append((sql, args))
        if "FROM teams" in sql and "SELECT id FROM teams" in sql:
            return TEAM_ID if self._existing_personal_team else None
        if "INSERT INTO teams" in sql:
            return TEAM_ID
        if "roles_catalog" in sql:
            return uuid4()
        return None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.executed.append((sql, args))
        return []

    async def execute(self, sql: str, *args: Any) -> None:
        self.executed.append((sql, args))

    def sql_containing(self, needle: str) -> list[str]:
        return [sql for sql, _ in self.executed if needle in sql]


def _pool_for(conn: _FakeConn) -> Any:
    """Minimal pool whose system_tx(...) context manager yields ``conn``."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool._mem_mcp_ctx = ctx
    return pool


@pytest.fixture
def user() -> CognitoUserSummary:
    return CognitoUserSummary(
        sub="e163edea-f0c1-70bb-fe3a-bae5f6e4268e",
        username="google_1234567890",
        email="manasipatil29102005@gmail.com",
        email_verified=True,
        workspace_domain=None,
        enabled=True,
        user_status="EXTERNAL_PROVIDER",
        identities_json='[{"providerName": "Google", "userId": "1234567890"}]',
    )


@pytest.fixture
def audit() -> Any:
    a = MagicMock()
    a.audit = AsyncMock()
    return a


@pytest.fixture(autouse=True)
def _patch_system_tx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route system_tx(pool) to the fake connection hung off the pool."""
    import mem_mcp.jobs.reconcile_signups as mod

    monkeypatch.setattr(mod, "system_tx", lambda pool: pool._mem_mcp_ctx)


@pytest.mark.asyncio
async def test_provision_creates_personal_team(user: CognitoUserSummary, audit: Any) -> None:
    """THE regression: a provisioned tenant must get a personal team."""
    conn = _FakeConn()
    ok, err = await _provision_one(_pool_for(conn), user, audit)

    assert ok, err
    created = conn.sql_containing("INSERT INTO teams")
    assert created, (
        "provisioning created no personal team; the tenant exists but MCP is "
        "unusable for them (no team resolves, no role held)"
    )
    name_arg = next(args for sql, args in conn.executed if "INSERT INTO teams" in sql)[0]
    assert name_arg == f"Personal — {user.email}"


@pytest.mark.asyncio
async def test_provision_grants_owner_role(user: CognitoUserSummary, audit: Any) -> None:
    """Without the role assignment, calls fail 'lacks required permission'."""
    conn = _FakeConn()
    await _provision_one(_pool_for(conn), user, audit)
    assert conn.sql_containing(
        "INSERT INTO team_role_assignments"
    ), "no owner role granted on the personal team"


@pytest.mark.asyncio
async def test_provision_sets_default_team_id(user: CognitoUserSummary, audit: Any) -> None:
    """Without default_team_id, memory_* calls fail with team_required."""
    conn = _FakeConn()
    await _provision_one(_pool_for(conn), user, audit)
    assert conn.sql_containing(
        "default_team_id"
    ), "default_team_id never set on the tenant's identity"


@pytest.mark.asyncio
async def test_provision_sets_rls_guc_before_team_insert(
    user: CognitoUserSummary, audit: Any
) -> None:
    """teams_insert RLS needs the tenant GUC; system_tx leaves it unset.

    Without the SET LOCAL the INSERT raises InsufficientPrivilegeError, so
    ordering matters, not just presence.
    """
    conn = _FakeConn()
    await _provision_one(_pool_for(conn), user, audit)

    order = [sql for sql, _ in conn.executed]
    guc = next(i for i, s in enumerate(order) if "set_config" in s)
    insert = next(i for i, s in enumerate(order) if "INSERT INTO teams" in s)
    assert guc < insert, "tenant GUC must be set before the teams INSERT"


@pytest.mark.asyncio
async def test_provision_is_idempotent_when_team_exists(
    user: CognitoUserSummary, audit: Any
) -> None:
    """Re-provisioning must not mint a second personal team (teams.name has no UNIQUE)."""
    conn = _FakeConn(existing_personal_team=True)
    await _provision_one(_pool_for(conn), user, audit)
    assert not conn.sql_containing(
        "INSERT INTO teams"
    ), "created a duplicate personal team for a tenant that already had one"
