"""Tests for bootstrap_first_system_admin env-driven bootstrap mechanism."""
from __future__ import annotations

import pytest


@pytest.mark.skipif(
    True,
    reason="Requires live DB fixture (pg_pool + setup_two_tenants + system_tx) — see notes below",
)
class TestBootstrapFirstSystemAdmin:
    """Test suite for idempotent first-admin bootstrap.

    NOTE: Full bootstrap tests require a live-DB fixture that can:
      1. Insert test tenants + tenant_identities
      2. Run system_tx queries to check system_state
      3. Verify tenant_system_roles inserts

    This fixture infrastructure does not yet exist in the test suite.
    To implement: extend tests/conftest.py with:
      - setup_tenant_with_identity(pg_pool, email) fixture returning (tenant_id, identity_id)
      - A pool-based fixture that provides pg_pool + monkeypatch for env vars

    Tests below are template specs awaiting fixture implementation.
    """

    @pytest.mark.asyncio
    async def test_bootstrap_skips_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Clear env var → bootstrap returns skipped status."""
        # fixture: pg_pool, monkeypatch
        # monkeypatch.delenv("BOOTSTRAP_SYSTEM_ADMIN_EMAIL", raising=False)
        # result = await bootstrap_first_system_admin(pg_pool)
        # assert result["status"] == "skipped"
        # assert result["reason"] == "env_not_set"
        pass

    @pytest.mark.asyncio
    async def test_bootstrap_grants_when_email_matches_identity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Email matches identity → role granted + system_state marker created."""
        # fixture: pg_pool, setup_tenant_with_identity("admin@example.com"), monkeypatch
        # monkeypatch.setenv("BOOTSTRAP_SYSTEM_ADMIN_EMAIL", "admin@example.com")
        # result = await bootstrap_first_system_admin(pg_pool)
        # assert result["status"] == "granted"
        # assert result["email"] == "admin@example.com"
        # Verify: SELECT 1 FROM tenant_system_roles WHERE tenant_id = ... AND role = 'system_admin'
        # Verify: SELECT 1 FROM system_state WHERE key = 'bootstrap_first_admin'
        pass

    @pytest.mark.asyncio
    async def test_bootstrap_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Run twice → second returns already_done, exactly one role row."""
        # fixture: pg_pool, setup_tenant_with_identity("admin@example.com"), monkeypatch
        # monkeypatch.setenv("BOOTSTRAP_SYSTEM_ADMIN_EMAIL", "admin@example.com")
        # result1 = await bootstrap_first_system_admin(pg_pool)
        # result2 = await bootstrap_first_system_admin(pg_pool)
        # assert result1["status"] == "granted"
        # assert result2["status"] == "already_done"
        # Verify: COUNT(*) FROM tenant_system_roles WHERE role = 'system_admin' == 1
        pass

    @pytest.mark.asyncio
    async def test_bootstrap_not_found_when_email_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Email doesn't match any identity → not_found status, no rows inserted."""
        # fixture: pg_pool, monkeypatch
        # monkeypatch.setenv("BOOTSTRAP_SYSTEM_ADMIN_EMAIL", "nonexistent@example.com")
        # result = await bootstrap_first_system_admin(pg_pool)
        # assert result["status"] == "not_found"
        # assert result["email"] == "nonexistent@example.com"
        # Verify: system_state has no row with key='bootstrap_first_admin'
        pass
