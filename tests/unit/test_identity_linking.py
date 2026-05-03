"""Tests for identity.linking module (T-7.10)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mem_mcp.identity.linking import (
    CognitoTokenExchanger,
    CognitoTokens,
    CognitoUserInfo,
    CognitoUserInfoFetcher,
    CompleteLinkResult,
    LinkingError,
    StartLinkResult,
    complete_link,
    sign_state,
    start_link,
    verify_state,
)


class TestSignVerifyState:
    """Tests for sign_state + verify_state HMAC functions."""

    def test_sign_then_verify_roundtrip(self) -> None:
        """Payload survives sign + verify."""
        secret = "test-secret-key"
        payload = {
            "tenant_id": "123e4567-e89b-12d3-a456-426614174000",
            "nonce": "abc123",
            "exp": 1700000000,
        }
        signed = sign_state(payload, secret)
        result = verify_state(signed, secret)
        assert result == payload

    def test_verify_rejects_tampered_signature(self) -> None:
        """Tampered signature → None."""
        secret = "test-secret-key"
        payload = {
            "tenant_id": "123e4567-e89b-12d3-a456-426614174000",
            "nonce": "abc123",
            "exp": 1700000000,
        }
        signed = sign_state(payload, secret)
        # Flip a byte in the signature part (after the last dot)
        parts = signed.rsplit(".", 1)
        tampered = parts[0] + ".tampered_signature_data"
        assert verify_state(tampered, secret) is None

    def test_verify_rejects_tampered_payload(self) -> None:
        """Tampered payload → None."""
        secret = "test-secret-key"
        payload = {
            "tenant_id": "123e4567-e89b-12d3-a456-426614174000",
            "nonce": "abc123",
            "exp": 1700000000,
        }
        signed = sign_state(payload, secret)
        # Flip a byte in the payload part (before the last dot)
        parts = signed.rsplit(".", 1)
        tampered = "eyJ0YW1wZXJlZCI6IHRydWV9." + parts[1]
        assert verify_state(tampered, secret) is None

    def test_verify_rejects_wrong_secret(self) -> None:
        """Different secret → None."""
        secret = "test-secret-key"
        payload = {
            "tenant_id": "123e4567-e89b-12d3-a456-426614174000",
            "nonce": "abc123",
            "exp": 1700000000,
        }
        signed = sign_state(payload, secret)
        assert verify_state(signed, "wrong-secret") is None


def _patch_tenant_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    """Patch tenant_tx to yield our fake conn."""

    @asynccontextmanager
    async def fake_tenant_tx(pool: Any, tenant_id: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.identity.linking.tenant_tx", fake_tenant_tx)


class TestStartLink:
    """Tests for start_link function."""

    @pytest.mark.asyncio
    async def test_start_link_inserts_link_state_and_returns_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """start_link inserts link_state row and returns authorize URL."""
        pool = MagicMock()
        tenant_id = uuid4()
        hmac_secret = "test-secret"
        cognito_authorize_base_url = "https://cognito.example.com/oauth2/authorize"
        cognito_client_id = "test-client-id"
        redirect_uri = "https://app.example.com/auth/callback"

        conn = AsyncMock()
        _patch_tenant_tx(monkeypatch, conn)

        result = await start_link(
            pool,
            tenant_id,
            hmac_secret=hmac_secret,
            cognito_authorize_base_url=cognito_authorize_base_url,
            cognito_client_id=cognito_client_id,
            redirect_uri=redirect_uri,
            ttl_seconds=600,
        )

        # Verify result type
        assert isinstance(result, StartLinkResult)
        assert result.authorize_url.startswith(cognito_authorize_base_url)
        assert cognito_client_id in result.authorize_url
        assert result.signed_state is not None
        assert result.nonce is not None

        # Verify INSERT was called
        conn.execute.assert_called_once()
        call_args = conn.execute.call_args
        assert "INSERT INTO link_state" in call_args[0][0]


class TestCompleteLink:
    """Tests for complete_link function."""

    def _make_fake_token_exchanger(self, tokens: CognitoTokens) -> CognitoTokenExchanger:
        """Create a fake token exchanger."""

        class FakeExchanger:
            async def exchange_code(self, code: str, redirect_uri: str) -> CognitoTokens:
                return tokens

        return FakeExchanger()

    def _make_fake_user_info_fetcher(self, user_info: CognitoUserInfo) -> CognitoUserInfoFetcher:
        """Create a fake user info fetcher."""

        class FakeFetcher:
            async def get_user_info(self, id_token: str) -> CognitoUserInfo:
                return user_info

        return FakeFetcher()

    @pytest.mark.asyncio
    async def test_complete_link_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """complete_link succeeds with valid state, cookie, session."""
        pool = MagicMock()
        tenant_id = uuid4()
        identity_id = uuid4()
        hmac_secret = "test-secret"

        # Create valid signed state
        payload = {
            "tenant_id": str(tenant_id),
            "nonce": "test-nonce",
            "exp": int((datetime.now(UTC) + timedelta(seconds=600)).timestamp()),
        }
        signed_state = sign_state(payload, hmac_secret)

        # Mock connection
        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "nonce": "test-nonce",
            "tenant_id": tenant_id,
            "expires_at": datetime.now(UTC) + timedelta(seconds=600),
            "consumed_at": None,
        }
        conn.fetchval.return_value = None  # cognito_sub not already linked
        conn.fetchval.side_effect = [
            None,
            identity_id,
        ]  # First for existing check, second for INSERT RETURNING
        _patch_tenant_tx(monkeypatch, conn)

        # Mock audit
        audit = AsyncMock()

        # Mock Cognito
        tokens = CognitoTokens(
            access_token="access",
            id_token="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJjb2duaXRvX3N1YiI6InN1YjEyMyIsImNvZ25pdG9fdXNlcm5hbWUiOiJ1c2VyMSIsImVtYWlsIjoiZ2lkZEBlbWFpbC5jb20iLCJwcm92aWRlciI6ImNvZ25pdG8iLCJwcm92aWRlcl91c2VyX2lkIjoicHVpZDEifQ.sig",
            refresh_token="refresh",
            expires_in=3600,
        )
        user_info = CognitoUserInfo(
            cognito_sub="sub123",
            cognito_username="user1",
            email="giddy@email.com",
            provider="cognito",
            provider_user_id="puid1",
        )

        exchanger = self._make_fake_token_exchanger(tokens)
        fetcher = self._make_fake_user_info_fetcher(user_info)

        result = await complete_link(
            pool,
            signed_state=signed_state,
            cookie_nonce="test-nonce",
            session_tenant_id=tenant_id,
            code="auth-code",
            hmac_secret=hmac_secret,
            redirect_uri="https://app.example.com/auth/callback",
            token_exchanger=exchanger,
            user_info_fetcher=fetcher,
            audit=audit,
            request_id="req-123",
        )

        assert isinstance(result, CompleteLinkResult)
        assert result.identity_id is not None

    @pytest.mark.asyncio
    async def test_complete_link_invalid_signed_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invalid HMAC → LinkingError('invalid_state')."""
        pool = MagicMock()
        tenant_id = uuid4()

        conn = AsyncMock()
        _patch_tenant_tx(monkeypatch, conn)

        audit = AsyncMock()

        with pytest.raises(LinkingError) as exc_info:
            await complete_link(
                pool,
                signed_state="invalid.state.data",
                cookie_nonce="test-nonce",
                session_tenant_id=tenant_id,
                code="auth-code",
                hmac_secret="test-secret",
                redirect_uri="https://app.example.com/auth/callback",
                token_exchanger=AsyncMock(),
                user_info_fetcher=AsyncMock(),
                audit=audit,
                request_id="req-123",
            )

        assert exc_info.value.code == "invalid_state"

    @pytest.mark.asyncio
    async def test_complete_link_cookie_mismatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cookie nonce != state nonce → LinkingError('cookie_mismatch')."""
        pool = MagicMock()
        tenant_id = uuid4()
        hmac_secret = "test-secret"

        payload = {
            "tenant_id": str(tenant_id),
            "nonce": "state-nonce",
            "exp": int((datetime.now(UTC) + timedelta(seconds=600)).timestamp()),
        }
        signed_state = sign_state(payload, hmac_secret)

        conn = AsyncMock()
        _patch_tenant_tx(monkeypatch, conn)

        audit = AsyncMock()

        with pytest.raises(LinkingError) as exc_info:
            await complete_link(
                pool,
                signed_state=signed_state,
                cookie_nonce="cookie-nonce",  # Mismatch
                session_tenant_id=tenant_id,
                code="auth-code",
                hmac_secret=hmac_secret,
                redirect_uri="https://app.example.com/auth/callback",
                token_exchanger=AsyncMock(),
                user_info_fetcher=AsyncMock(),
                audit=audit,
                request_id="req-123",
            )

        assert exc_info.value.code == "cookie_mismatch"

    @pytest.mark.asyncio
    async def test_complete_link_session_mismatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Different tenant in session vs state → LinkingError('session_mismatch')."""
        pool = MagicMock()
        state_tenant_id = uuid4()
        session_tenant_id = uuid4()
        hmac_secret = "test-secret"

        payload = {
            "tenant_id": str(state_tenant_id),
            "nonce": "test-nonce",
            "exp": int((datetime.now(UTC) + timedelta(seconds=600)).timestamp()),
        }
        signed_state = sign_state(payload, hmac_secret)

        conn = AsyncMock()
        _patch_tenant_tx(monkeypatch, conn)

        audit = AsyncMock()

        with pytest.raises(LinkingError) as exc_info:
            await complete_link(
                pool,
                signed_state=signed_state,
                cookie_nonce="test-nonce",
                session_tenant_id=session_tenant_id,  # Different tenant
                code="auth-code",
                hmac_secret=hmac_secret,
                redirect_uri="https://app.example.com/auth/callback",
                token_exchanger=AsyncMock(),
                user_info_fetcher=AsyncMock(),
                audit=audit,
                request_id="req-123",
            )

        assert exc_info.value.code == "session_mismatch"

    @pytest.mark.asyncio
    async def test_complete_link_expired_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Expired link_state → LinkingError('expired')."""
        pool = MagicMock()
        tenant_id = uuid4()
        hmac_secret = "test-secret"

        payload = {
            "tenant_id": str(tenant_id),
            "nonce": "test-nonce",
            "exp": int((datetime.now(UTC) + timedelta(seconds=600)).timestamp()),
        }
        signed_state = sign_state(payload, hmac_secret)

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "nonce": "test-nonce",
            "tenant_id": tenant_id,
            "expires_at": datetime.now(UTC) - timedelta(seconds=1),  # Expired
            "consumed_at": None,
        }
        _patch_tenant_tx(monkeypatch, conn)

        audit = AsyncMock()

        with pytest.raises(LinkingError) as exc_info:
            await complete_link(
                pool,
                signed_state=signed_state,
                cookie_nonce="test-nonce",
                session_tenant_id=tenant_id,
                code="auth-code",
                hmac_secret=hmac_secret,
                redirect_uri="https://app.example.com/auth/callback",
                token_exchanger=AsyncMock(),
                user_info_fetcher=AsyncMock(),
                audit=audit,
                request_id="req-123",
            )

        assert exc_info.value.code == "expired"

    @pytest.mark.asyncio
    async def test_complete_link_already_consumed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Already-consumed link_state → LinkingError('consumed')."""
        pool = MagicMock()
        tenant_id = uuid4()
        hmac_secret = "test-secret"

        payload = {
            "tenant_id": str(tenant_id),
            "nonce": "test-nonce",
            "exp": int((datetime.now(UTC) + timedelta(seconds=600)).timestamp()),
        }
        signed_state = sign_state(payload, hmac_secret)

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "nonce": "test-nonce",
            "tenant_id": tenant_id,
            "expires_at": datetime.now(UTC) + timedelta(seconds=600),
            "consumed_at": datetime.now(UTC) - timedelta(seconds=60),  # Already consumed
        }
        _patch_tenant_tx(monkeypatch, conn)

        audit = AsyncMock()

        with pytest.raises(LinkingError) as exc_info:
            await complete_link(
                pool,
                signed_state=signed_state,
                cookie_nonce="test-nonce",
                session_tenant_id=tenant_id,
                code="auth-code",
                hmac_secret=hmac_secret,
                redirect_uri="https://app.example.com/auth/callback",
                token_exchanger=AsyncMock(),
                user_info_fetcher=AsyncMock(),
                audit=audit,
                request_id="req-123",
            )

        assert exc_info.value.code == "consumed"

    @pytest.mark.asyncio
    async def test_complete_link_state_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing link_state row → LinkingError('state_not_found')."""
        pool = MagicMock()
        tenant_id = uuid4()
        hmac_secret = "test-secret"

        payload = {
            "tenant_id": str(tenant_id),
            "nonce": "test-nonce",
            "exp": int((datetime.now(UTC) + timedelta(seconds=600)).timestamp()),
        }
        signed_state = sign_state(payload, hmac_secret)

        conn = AsyncMock()
        conn.fetchrow.return_value = None  # Not found
        _patch_tenant_tx(monkeypatch, conn)

        audit = AsyncMock()

        with pytest.raises(LinkingError) as exc_info:
            await complete_link(
                pool,
                signed_state=signed_state,
                cookie_nonce="test-nonce",
                session_tenant_id=tenant_id,
                code="auth-code",
                hmac_secret=hmac_secret,
                redirect_uri="https://app.example.com/auth/callback",
                token_exchanger=AsyncMock(),
                user_info_fetcher=AsyncMock(),
                audit=audit,
                request_id="req-123",
            )

        assert exc_info.value.code == "state_not_found"

    @pytest.mark.asyncio
    async def test_complete_link_sub_already_linked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cognito sub already in tenant_identities → LinkingError('sub_already_linked')."""
        pool = MagicMock()
        tenant_id = uuid4()
        hmac_secret = "test-secret"

        payload = {
            "tenant_id": str(tenant_id),
            "nonce": "test-nonce",
            "exp": int((datetime.now(UTC) + timedelta(seconds=600)).timestamp()),
        }
        signed_state = sign_state(payload, hmac_secret)

        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "nonce": "test-nonce",
            "tenant_id": tenant_id,
            "expires_at": datetime.now(UTC) + timedelta(seconds=600),
            "consumed_at": None,
        }
        # Simulate UNIQUE constraint violation
        conn.execute.side_effect = Exception("duplicate key value violates unique constraint")
        _patch_tenant_tx(monkeypatch, conn)

        audit = AsyncMock()

        tokens = CognitoTokens(
            access_token="access",
            id_token="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJjb2duaXRvX3N1YiI6InN1YjEyMyIsImNvZ25pdG9fdXNlcm5hbWUiOiJ1c2VyMSIsImVtYWlsIjoiZ2lkZEBlbWFpbC5jb20iLCJwcm92aWRlciI6ImNvZ25pdG8iLCJwcm92aWRlcl91c2VyX2lkIjoicHVpZDEifQ.sig",
            refresh_token="refresh",
            expires_in=3600,
        )
        user_info = CognitoUserInfo(
            cognito_sub="sub123",
            cognito_username="user1",
            email="giddy@email.com",
            provider="cognito",
            provider_user_id="puid1",
        )

        exchanger = self._make_fake_token_exchanger(tokens)
        fetcher = self._make_fake_user_info_fetcher(user_info)

        with pytest.raises(LinkingError) as exc_info:
            await complete_link(
                pool,
                signed_state=signed_state,
                cookie_nonce="test-nonce",
                session_tenant_id=tenant_id,
                code="auth-code",
                hmac_secret=hmac_secret,
                redirect_uri="https://app.example.com/auth/callback",
                token_exchanger=exchanger,
                user_info_fetcher=fetcher,
                audit=audit,
                request_id="req-123",
            )

        assert exc_info.value.code == "sub_already_linked"
