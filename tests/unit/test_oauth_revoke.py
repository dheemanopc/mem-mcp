"""Tests for oauth.revoke module (T-7.13)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mem_mcp.oauth.revoke import (
    CognitoClientDeleter,
    RevokeClientError,
    revoke_client,
)


def _patch_system_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    """Patch system_tx to yield our fake conn."""

    @asynccontextmanager
    async def fake_system_tx(pool: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.oauth.revoke.system_tx", fake_system_tx)


class FakeCognitoClientDeleter:
    """Fake Cognito client deleter for testing."""

    def __init__(self) -> None:
        self.deleted_clients: list[str] = []
        self._delete_mock = AsyncMock()

    async def delete_user_pool_client(self, client_id: str) -> None:
        await self._delete_mock(client_id)
        self.deleted_clients.append(client_id)


class TestRevokeClient:
    """Tests for revoke_client function."""

    @pytest.mark.asyncio
    async def test_revoke_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """revoke_client succeeds: disabled=False, tenant matches."""
        pool = MagicMock()
        tenant_id = uuid4()
        client_id = "test-client-123"

        conn = AsyncMock()
        # Mock fetchrow to return existing, non-disabled client
        conn.fetchrow.return_value = {
            "id": client_id,
            "tenant_id": tenant_id,
            "disabled": False,
        }
        _patch_system_tx(monkeypatch, conn)

        deleter = FakeCognitoClientDeleter()
        audit = AsyncMock()

        # Should not raise
        await revoke_client(
            pool,
            tenant_id=tenant_id,
            client_id=client_id,
            deleter=deleter,
            audit=audit,
            request_id="req-123",
        )

        # Verify UPDATE was called
        assert conn.execute.call_count == 1
        call_args = conn.execute.call_args
        assert "disabled = true" in call_args[0][0]

        # Verify deleter was called
        assert len(deleter.deleted_clients) == 1
        assert deleter.deleted_clients[0] == client_id

        # Verify audit was called
        assert audit.audit.call_count == 1
        audit_call = audit.audit.call_args
        assert audit_call[1]["action"] == "oauth.client_revoked"
        assert audit_call[1]["result"] == "success"
        assert audit_call[1]["target_id"] == client_id
        assert audit_call[1]["target_kind"] == "oauth_client"
        assert audit_call[1]["tenant_id"] == tenant_id
        assert audit_call[1]["request_id"] == "req-123"

    @pytest.mark.asyncio
    async def test_revoke_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """revoke_client raises RevokeClientError('not_found') when client doesn't exist."""
        pool = MagicMock()
        tenant_id = uuid4()
        client_id = "nonexistent-client"

        conn = AsyncMock()
        # Mock fetchrow to return None
        conn.fetchrow.return_value = None
        _patch_system_tx(monkeypatch, conn)

        deleter = FakeCognitoClientDeleter()
        audit = AsyncMock()

        with pytest.raises(RevokeClientError) as exc_info:
            await revoke_client(
                pool,
                tenant_id=tenant_id,
                client_id=client_id,
                deleter=deleter,
                audit=audit,
                request_id="req-123",
            )

        assert exc_info.value.code == "not_found"

        # Verify deleter and audit were NOT called
        assert len(deleter.deleted_clients) == 0
        assert audit.audit.call_count == 0

        # Verify UPDATE was NOT called
        assert conn.execute.call_count == 0

    @pytest.mark.asyncio
    async def test_revoke_wrong_tenant(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """revoke_client raises RevokeClientError('wrong_tenant') when tenant_id doesn't match."""
        pool = MagicMock()
        tenant_id = uuid4()
        other_tenant_id = uuid4()
        client_id = "test-client-123"

        conn = AsyncMock()
        # Mock fetchrow to return client with different tenant_id
        conn.fetchrow.return_value = {
            "id": client_id,
            "tenant_id": other_tenant_id,
            "disabled": False,
        }
        _patch_system_tx(monkeypatch, conn)

        deleter = FakeCognitoClientDeleter()
        audit = AsyncMock()

        with pytest.raises(RevokeClientError) as exc_info:
            await revoke_client(
                pool,
                tenant_id=tenant_id,
                client_id=client_id,
                deleter=deleter,
                audit=audit,
                request_id="req-123",
            )

        assert exc_info.value.code == "wrong_tenant"

        # Verify deleter and audit were NOT called
        assert len(deleter.deleted_clients) == 0
        assert audit.audit.call_count == 0

        # Verify UPDATE was NOT called
        assert conn.execute.call_count == 0

    @pytest.mark.asyncio
    async def test_revoke_already_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """revoke_client raises RevokeClientError('already_disabled') when client is already disabled."""
        pool = MagicMock()
        tenant_id = uuid4()
        client_id = "test-client-123"

        conn = AsyncMock()
        # Mock fetchrow to return disabled client
        conn.fetchrow.return_value = {
            "id": client_id,
            "tenant_id": tenant_id,
            "disabled": True,
        }
        _patch_system_tx(monkeypatch, conn)

        deleter = FakeCognitoClientDeleter()
        audit = AsyncMock()

        with pytest.raises(RevokeClientError) as exc_info:
            await revoke_client(
                pool,
                tenant_id=tenant_id,
                client_id=client_id,
                deleter=deleter,
                audit=audit,
                request_id="req-123",
            )

        assert exc_info.value.code == "already_disabled"

        # Verify deleter and audit were NOT called
        assert len(deleter.deleted_clients) == 0
        assert audit.audit.call_count == 0

        # Verify UPDATE was NOT called
        assert conn.execute.call_count == 0

    @pytest.mark.asyncio
    async def test_revoke_cognito_failure_continues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """revoke_client continues when Cognito deletion fails; DB row stays disabled."""
        pool = MagicMock()
        tenant_id = uuid4()
        client_id = "test-client-123"

        conn = AsyncMock()
        # Mock fetchrow to return existing, non-disabled client
        conn.fetchrow.return_value = {
            "id": client_id,
            "tenant_id": tenant_id,
            "disabled": False,
        }
        _patch_system_tx(monkeypatch, conn)

        # Create deleter that raises an exception
        deleter = AsyncMock(spec=CognitoClientDeleter)
        deleter.delete_user_pool_client.side_effect = RuntimeError("Cognito API error")

        audit = AsyncMock()

        # Should NOT raise despite Cognito failure
        await revoke_client(
            pool,
            tenant_id=tenant_id,
            client_id=client_id,
            deleter=deleter,
            audit=audit,
            request_id="req-123",
        )

        # Verify UPDATE was still called
        assert conn.execute.call_count == 1

        # Verify audit was still called
        assert audit.audit.call_count == 1
        audit_call = audit.audit.call_args
        assert audit_call[1]["action"] == "oauth.client_revoked"

        # Verify deleter was called (and failed)
        assert deleter.delete_user_pool_client.call_count == 1

    @pytest.mark.asyncio
    async def test_revoke_audits_with_correct_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """revoke_client audits with correct target_id and target_kind."""
        pool = MagicMock()
        tenant_id = uuid4()
        client_id = "my-oauth-client"

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": client_id,
            "tenant_id": tenant_id,
            "disabled": False,
        }
        _patch_system_tx(monkeypatch, conn)

        deleter = FakeCognitoClientDeleter()
        audit = AsyncMock()

        await revoke_client(
            pool,
            tenant_id=tenant_id,
            client_id=client_id,
            deleter=deleter,
            audit=audit,
            request_id="req-456",
        )

        # Verify audit was called with exact target values
        assert audit.audit.call_count == 1
        audit_call = audit.audit.call_args
        assert audit_call[1]["target_id"] == "my-oauth-client"
        assert audit_call[1]["target_kind"] == "oauth_client"
