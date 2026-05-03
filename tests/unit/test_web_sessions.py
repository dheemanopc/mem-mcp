"""Tests for mem_mcp.web.sessions (T-8.2)."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mem_mcp.web.sessions import (
    SESSION_TOUCH_THRESHOLD,
    SESSION_TTL,
    CreateSessionResult,
    SessionContext,
    create_session,
    lookup_session,
    mint_session_value,
    revoke_session,
)


def _patch_system_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    """Patch system_tx to yield our fake conn."""

    @asynccontextmanager
    async def fake_system_tx(pool: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.web.sessions.system_tx", fake_system_tx)


# --------------------------------------------------------------------------
# Tests for mint_session_value
# --------------------------------------------------------------------------


class TestMintSessionValue:
    def test_returns_tuple_of_two_strings(self) -> None:
        """mint_session_value returns (raw, sha)."""
        raw, sha = mint_session_value()
        assert isinstance(raw, str)
        assert isinstance(sha, str)

    def test_raw_value_is_urlsafe(self) -> None:
        """mint_session_value raw value is urlsafe base64."""
        raw, _ = mint_session_value()
        # Should not raise
        import base64

        decoded = base64.urlsafe_b64decode(raw + "==")
        assert len(decoded) == 32

    def test_sha_is_sha256(self) -> None:
        """mint_session_value sha matches sha256(raw)."""
        raw, sha = mint_session_value()
        expected_sha = hashlib.sha256(raw.encode()).hexdigest()
        assert sha == expected_sha

    def test_unique_pairs(self) -> None:
        """mint_session_value returns unique values on each call."""
        pairs = [mint_session_value() for _ in range(10)]
        raws = [p[0] for p in pairs]
        # All raw values should be unique
        assert len(set(raws)) == 10


# --------------------------------------------------------------------------
# Tests for create_session
# --------------------------------------------------------------------------


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_create_session_inserts_and_returns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """create_session inserts row and returns CreateSessionResult."""
        pool = MagicMock()
        conn = AsyncMock()
        _patch_system_tx(monkeypatch, conn)

        tenant_id = uuid4()
        identity_id = uuid4()

        def now_fn() -> datetime:
            return datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)

        result = await create_session(
            pool,
            tenant_id=tenant_id,
            identity_id=identity_id,
            user_agent="Mozilla/5.0",
            ip_address="192.168.1.1",
            now=now_fn,
        )

        # Check return type
        assert isinstance(result, CreateSessionResult)
        assert isinstance(result.raw_value, str)
        assert isinstance(result.expires_at, datetime)

        # Check expires_at is 7 days from now
        expected_expires = now_fn() + SESSION_TTL
        assert result.expires_at == expected_expires

        # Check INSERT was called
        assert conn.execute.call_count == 1
        call_args = conn.execute.call_args
        assert "INSERT INTO web_sessions" in call_args[0][0]
        # Verify params
        assert call_args[0][2] == tenant_id
        assert call_args[0][3] == identity_id
        assert call_args[0][4] == "Mozilla/5.0"
        assert call_args[0][5] == "192.168.1.1"
        assert call_args[0][6] == expected_expires

    @pytest.mark.asyncio
    async def test_create_session_with_none_user_agent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """create_session allows None user_agent."""
        pool = MagicMock()
        conn = AsyncMock()
        _patch_system_tx(monkeypatch, conn)

        tenant_id = uuid4()
        identity_id = uuid4()

        await create_session(
            pool,
            tenant_id=tenant_id,
            identity_id=identity_id,
            user_agent=None,
            ip_address=None,
        )

        # INSERT should have been called with None values
        assert conn.execute.call_count == 1
        call_args = conn.execute.call_args
        assert call_args[0][4] is None
        assert call_args[0][5] is None


# --------------------------------------------------------------------------
# Tests for lookup_session
# --------------------------------------------------------------------------


class TestLookupSession:
    @pytest.mark.asyncio
    async def test_lookup_session_valid_returns_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """lookup_session returns SessionContext for valid session."""
        pool = MagicMock()
        conn = AsyncMock()
        _patch_system_tx(monkeypatch, conn)

        tenant_id = uuid4()
        identity_id = uuid4()

        def now_fn() -> datetime:
            return datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)

        # Setup mock fetchrow to return a valid session
        future_expires = now_fn() + SESSION_TTL
        conn.fetchrow.return_value = {
            "tenant_id": tenant_id,
            "identity_id": identity_id,
            "expires_at": future_expires,
            "revoked_at": None,
            "last_seen_at": now_fn(),
        }

        raw, _ = mint_session_value()
        result = await lookup_session(pool, raw, now=now_fn)

        assert result is not None
        assert isinstance(result, SessionContext)
        assert result.tenant_id == tenant_id
        assert result.identity_id == identity_id

    @pytest.mark.asyncio
    async def test_lookup_session_missing_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """lookup_session returns None for unknown session."""
        pool = MagicMock()
        conn = AsyncMock()
        _patch_system_tx(monkeypatch, conn)

        conn.fetchrow.return_value = None

        raw, _ = mint_session_value()
        result = await lookup_session(pool, raw)

        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_session_expired_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """lookup_session returns None for expired session."""
        pool = MagicMock()
        conn = AsyncMock()
        _patch_system_tx(monkeypatch, conn)

        tenant_id = uuid4()

        def now_fn() -> datetime:
            return datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)

        past_expires = now_fn() - timedelta(days=1)

        conn.fetchrow.return_value = {
            "tenant_id": tenant_id,
            "identity_id": uuid4(),
            "expires_at": past_expires,
            "revoked_at": None,
            "last_seen_at": now_fn(),
        }

        raw, _ = mint_session_value()
        result = await lookup_session(pool, raw, now=now_fn)

        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_session_revoked_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """lookup_session returns None for revoked session."""
        pool = MagicMock()
        conn = AsyncMock()
        _patch_system_tx(monkeypatch, conn)

        tenant_id = uuid4()

        def now_fn() -> datetime:
            return datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)

        future_expires = now_fn() + SESSION_TTL

        conn.fetchrow.return_value = {
            "tenant_id": tenant_id,
            "identity_id": uuid4(),
            "expires_at": future_expires,
            "revoked_at": now_fn() - timedelta(minutes=1),
            "last_seen_at": now_fn(),
        }

        raw, _ = mint_session_value()
        result = await lookup_session(pool, raw, now=now_fn)

        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_session_touches_stale_last_seen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """lookup_session updates last_seen_at if > 60s old."""
        pool = MagicMock()
        conn = AsyncMock()
        _patch_system_tx(monkeypatch, conn)

        tenant_id = uuid4()
        identity_id = uuid4()

        def now_fn() -> datetime:
            return datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)

        stale_seen = now_fn() - SESSION_TOUCH_THRESHOLD - timedelta(seconds=1)
        future_expires = now_fn() + SESSION_TTL

        conn.fetchrow.return_value = {
            "tenant_id": tenant_id,
            "identity_id": identity_id,
            "expires_at": future_expires,
            "revoked_at": None,
            "last_seen_at": stale_seen,
        }

        raw, _ = mint_session_value()
        result = await lookup_session(pool, raw, now=now_fn)

        assert result is not None
        # Should have called UPDATE
        assert conn.execute.call_count == 1
        call_args = conn.execute.call_args
        assert "UPDATE web_sessions" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_lookup_session_skips_touch_when_recent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """lookup_session skips UPDATE if last_seen < 60s ago."""
        pool = MagicMock()
        conn = AsyncMock()
        _patch_system_tx(monkeypatch, conn)

        tenant_id = uuid4()
        identity_id = uuid4()

        def now_fn() -> datetime:
            return datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)

        recent_seen = now_fn() - timedelta(seconds=30)
        future_expires = now_fn() + SESSION_TTL

        conn.fetchrow.return_value = {
            "tenant_id": tenant_id,
            "identity_id": identity_id,
            "expires_at": future_expires,
            "revoked_at": None,
            "last_seen_at": recent_seen,
        }

        raw, _ = mint_session_value()
        result = await lookup_session(pool, raw, now=now_fn)

        assert result is not None
        # Should NOT have called UPDATE
        assert conn.execute.call_count == 0


# --------------------------------------------------------------------------
# Tests for revoke_session
# --------------------------------------------------------------------------


class TestRevokeSession:
    @pytest.mark.asyncio
    async def test_revoke_session_marks_revoked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """revoke_session marks session revoked."""
        pool = MagicMock()
        conn = AsyncMock()
        _patch_system_tx(monkeypatch, conn)

        raw, _ = mint_session_value()
        await revoke_session(pool, raw)

        # Should have called UPDATE
        assert conn.execute.call_count == 1
        call_args = conn.execute.call_args
        assert "UPDATE web_sessions" in call_args[0][0]
        assert "revoked_at" in call_args[0][0]
