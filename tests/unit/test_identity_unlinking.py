"""Tests for identity.unlinking module (T-7.11)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mem_mcp.identity.unlinking import (
    CognitoAdminDeleter,
    PromotionError,
    UnlinkingError,
    promote_primary,
    unlink_identity,
)


def _patch_tenant_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    """Patch tenant_tx to yield our fake conn."""

    @asynccontextmanager
    async def fake_tenant_tx(pool: Any, tenant_id: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.identity.unlinking.tenant_tx", fake_tenant_tx)


class TestUnlinkIdentity:
    """Tests for unlink_identity function."""

    def _make_fake_deleter(self) -> CognitoAdminDeleter:
        """Create a fake Cognito deleter."""

        class FakeDeleter:
            async def admin_delete_user(self, cognito_username: str) -> None:
                pass

        return FakeDeleter()

    @pytest.mark.asyncio
    async def test_unlink_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """unlink_identity succeeds for non-primary, >=2 identities."""
        pool = MagicMock()
        tenant_id = uuid4()
        identity_id = uuid4()

        conn = AsyncMock()
        # Mock fetchval for count check (returns 2 identities)
        conn.fetchval.return_value = 2
        # Mock fetchrow for identity fetch
        conn.fetchrow.return_value = {
            "id": identity_id,
            "cognito_username": "user1",
            "is_primary": False,
        }
        _patch_tenant_tx(monkeypatch, conn)

        deleter = self._make_fake_deleter()
        audit = AsyncMock()

        # Should not raise
        await unlink_identity(
            pool,
            tenant_id=tenant_id,
            identity_id=identity_id,
            deleter=deleter,
            audit=audit,
            request_id="req-123",
        )

        # Verify DELETE was called
        assert conn.execute.call_count >= 1

    @pytest.mark.asyncio
    async def test_unlink_last_identity_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cannot unlink last identity → UnlinkingError('last_identity')."""
        pool = MagicMock()
        tenant_id = uuid4()
        identity_id = uuid4()

        conn = AsyncMock()
        # Only 1 identity
        conn.fetchval.return_value = 1
        _patch_tenant_tx(monkeypatch, conn)

        deleter = self._make_fake_deleter()
        audit = AsyncMock()

        with pytest.raises(UnlinkingError) as exc_info:
            await unlink_identity(
                pool,
                tenant_id=tenant_id,
                identity_id=identity_id,
                deleter=deleter,
                audit=audit,
                request_id="req-123",
            )

        assert exc_info.value.code == "last_identity"

    @pytest.mark.asyncio
    async def test_unlink_primary_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cannot unlink primary → UnlinkingError('is_primary')."""
        pool = MagicMock()
        tenant_id = uuid4()
        identity_id = uuid4()

        conn = AsyncMock()
        conn.fetchval.return_value = 2  # >=2 identities
        conn.fetchrow.return_value = {
            "id": identity_id,
            "cognito_username": "user1",
            "is_primary": True,  # Primary
        }
        _patch_tenant_tx(monkeypatch, conn)

        deleter = self._make_fake_deleter()
        audit = AsyncMock()

        with pytest.raises(UnlinkingError) as exc_info:
            await unlink_identity(
                pool,
                tenant_id=tenant_id,
                identity_id=identity_id,
                deleter=deleter,
                audit=audit,
                request_id="req-123",
            )

        assert exc_info.value.code == "is_primary"

    @pytest.mark.asyncio
    async def test_unlink_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Identity not found → UnlinkingError('not_found')."""
        pool = MagicMock()
        tenant_id = uuid4()
        identity_id = uuid4()

        conn = AsyncMock()
        conn.fetchval.return_value = 2  # >=2 identities
        conn.fetchrow.return_value = None  # Not found
        _patch_tenant_tx(monkeypatch, conn)

        deleter = self._make_fake_deleter()
        audit = AsyncMock()

        with pytest.raises(UnlinkingError) as exc_info:
            await unlink_identity(
                pool,
                tenant_id=tenant_id,
                identity_id=identity_id,
                deleter=deleter,
                audit=audit,
                request_id="req-123",
            )

        assert exc_info.value.code == "not_found"


class TestPromotePrimary:
    """Tests for promote_primary function."""

    @pytest.mark.asyncio
    async def test_promote_primary_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """promote_primary atomically updates is_primary flags."""
        pool = MagicMock()
        tenant_id = uuid4()
        identity_id = uuid4()

        conn = AsyncMock()
        # Mock fetchrow to verify target exists and is not already primary
        conn.fetchrow.return_value = {
            "id": identity_id,
            "is_primary": False,
        }
        _patch_tenant_tx(monkeypatch, conn)

        audit = AsyncMock()

        # Should not raise
        await promote_primary(
            pool,
            tenant_id=tenant_id,
            identity_id=identity_id,
            audit=audit,
            request_id="req-123",
        )

        # Verify UPDATE calls (set all to false, then target to true)
        assert conn.execute.call_count >= 2

    @pytest.mark.asyncio
    async def test_promote_already_primary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Already primary → PromotionError('already_primary')."""
        pool = MagicMock()
        tenant_id = uuid4()
        identity_id = uuid4()

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": identity_id,
            "is_primary": True,  # Already primary
        }
        _patch_tenant_tx(monkeypatch, conn)

        audit = AsyncMock()

        with pytest.raises(PromotionError) as exc_info:
            await promote_primary(
                pool,
                tenant_id=tenant_id,
                identity_id=identity_id,
                audit=audit,
                request_id="req-123",
            )

        assert exc_info.value.code == "already_primary"

    @pytest.mark.asyncio
    async def test_promote_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Identity not found → PromotionError('not_found')."""
        pool = MagicMock()
        tenant_id = uuid4()
        identity_id = uuid4()

        conn = AsyncMock()
        conn.fetchrow.return_value = None  # Not found
        _patch_tenant_tx(monkeypatch, conn)

        audit = AsyncMock()

        with pytest.raises(PromotionError) as exc_info:
            await promote_primary(
                pool,
                tenant_id=tenant_id,
                identity_id=identity_id,
                audit=audit,
                request_id="req-123",
            )

        assert exc_info.value.code == "not_found"
