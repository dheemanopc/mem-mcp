"""Tests for bootstrap_first_system_admin env-driven bootstrap mechanism."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from mem_mcp.bootstrap.system_admin import bootstrap_first_system_admin


@pytest.mark.integration
class TestBootstrapFirstSystemAdmin:
    """Test suite for idempotent first-admin bootstrap.

    Tests use MEM_MCP_TEST_DSN env var to run against a live test DB.
    Each test inserts test tenants + identities, then cleans up afterward.
    Pattern matches tests/unit/test_enterprise_schema.py.
    """

    @pytest.mark.asyncio
    async def test_bootstrap_skips_when_env_unset(
        self, pg_pool: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Clear env var → bootstrap returns skipped status."""
        # Remove the env var if set
        monkeypatch.delenv("BOOTSTRAP_SYSTEM_ADMIN_EMAIL", raising=False)

        result = await bootstrap_first_system_admin(pg_pool)
        assert result["status"] == "skipped"
        assert result["reason"] == "env_not_set"

    @pytest.mark.asyncio
    async def test_bootstrap_grants_when_email_matches_identity(
        self, pg_pool: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Email matches identity → role granted + system_state marker created."""
        tenant_id = uuid4()
        email = f"bootstrap-test-{uuid4()}@example.com"

        async with pg_pool.acquire() as conn:
            try:
                # Insert test tenant
                await conn.execute(
                    "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                    tenant_id,
                    f"tenant-{tenant_id}@test.invalid",
                )

                # Insert tenant_identity with target email
                await conn.execute(
                    """
                    INSERT INTO tenant_identities (tenant_id, cognito_sub, provider, email)
                    VALUES ($1, $2, $3, $4)
                    """,
                    tenant_id,
                    f"sub-{tenant_id}",
                    "google",
                    email,
                )

                # Set env var
                monkeypatch.setenv("BOOTSTRAP_SYSTEM_ADMIN_EMAIL", email)

                # Run bootstrap
                result = await bootstrap_first_system_admin(pg_pool)

                # Verify status
                assert result["status"] == "granted"
                assert result["email"] == email
                assert result["tenant_id"] == str(tenant_id)

                # Verify tenant_system_roles row exists
                role_exists = await conn.fetchval(
                    "SELECT 1 FROM tenant_system_roles WHERE tenant_id = $1 AND role = 'system_admin'",
                    tenant_id,
                )
                assert role_exists is not None

                # Verify system_state marker exists
                state_exists = await conn.fetchval(
                    "SELECT 1 FROM system_state WHERE key = 'bootstrap_first_admin'",
                )
                assert state_exists is not None
            finally:
                # Cleanup
                await conn.execute(
                    "DELETE FROM tenant_system_roles WHERE tenant_id = $1",
                    tenant_id,
                )
                await conn.execute("DELETE FROM tenant_identities WHERE tenant_id = $1", tenant_id)
                await conn.execute("DELETE FROM system_state WHERE key = 'bootstrap_first_admin'")
                await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)

    @pytest.mark.asyncio
    async def test_bootstrap_idempotent(
        self, pg_pool: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Run twice → second returns already_done, exactly one role row."""
        tenant_id = uuid4()
        email = f"bootstrap-idempotent-{uuid4()}@example.com"

        async with pg_pool.acquire() as conn:
            try:
                # Insert test tenant
                await conn.execute(
                    "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                    tenant_id,
                    f"tenant-{tenant_id}@test.invalid",
                )

                # Insert tenant_identity with target email
                await conn.execute(
                    """
                    INSERT INTO tenant_identities (tenant_id, cognito_sub, provider, email)
                    VALUES ($1, $2, $3, $4)
                    """,
                    tenant_id,
                    f"sub-{tenant_id}",
                    "google",
                    email,
                )

                # Set env var
                monkeypatch.setenv("BOOTSTRAP_SYSTEM_ADMIN_EMAIL", email)

                # First bootstrap call
                result1 = await bootstrap_first_system_admin(pg_pool)
                assert result1["status"] == "granted"

                # Second bootstrap call
                result2 = await bootstrap_first_system_admin(pg_pool)
                assert result2["status"] == "already_done"

                # Verify exactly ONE tenant_system_roles row (idempotent)
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM tenant_system_roles WHERE tenant_id = $1 AND role = 'system_admin'",
                    tenant_id,
                )
                assert count == 1
            finally:
                # Cleanup
                await conn.execute(
                    "DELETE FROM tenant_system_roles WHERE tenant_id = $1",
                    tenant_id,
                )
                await conn.execute("DELETE FROM tenant_identities WHERE tenant_id = $1", tenant_id)
                await conn.execute("DELETE FROM system_state WHERE key = 'bootstrap_first_admin'")
                await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)

    @pytest.mark.asyncio
    async def test_bootstrap_not_found_when_email_missing(
        self, pg_pool: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Email doesn't match any identity → not_found status, no rows inserted."""
        nonexistent_email = f"nonexistent-{uuid4()}@example.com"

        # Set env var to nonexistent email
        monkeypatch.setenv("BOOTSTRAP_SYSTEM_ADMIN_EMAIL", nonexistent_email)

        async with pg_pool.acquire() as conn:
            try:
                # Run bootstrap
                result = await bootstrap_first_system_admin(pg_pool)

                # Verify not_found status
                assert result["status"] == "not_found"
                assert result["email"] == nonexistent_email

                # Verify no system_state row was created for bootstrap
                state_exists = await conn.fetchval(
                    "SELECT 1 FROM system_state WHERE key = 'bootstrap_first_admin'",
                )
                assert state_exists is None
            finally:
                # Cleanup just in case
                await conn.execute("DELETE FROM system_state WHERE key = 'bootstrap_first_admin'")
