"""Tests for mem_mcp.web.routes (T-8.2)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mem_mcp.web.routes import make_web_router
from mem_mcp.web.sessions import SESSION_TTL


def _patch_system_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    """Patch system_tx to yield our fake conn."""

    @asynccontextmanager
    async def fake_system_tx(pool: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.web.routes.system_tx", fake_system_tx)


def _patch_create_session(monkeypatch: pytest.MonkeyPatch, result: Any = None) -> None:
    """Patch create_session to return a fake result."""
    if result is None:
        now = datetime.now(UTC)
        result = MagicMock(
            raw_value="test-session-value",
            expires_at=now + SESSION_TTL,
        )

    async def fake_create_session(*args: Any, **kwargs: Any) -> Any:
        return result

    monkeypatch.setattr("mem_mcp.web.routes.create_session", fake_create_session)


def _patch_revoke_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch revoke_session to be a no-op."""

    async def fake_revoke_session(pool: Any, raw_value: str) -> None:
        pass

    monkeypatch.setattr("mem_mcp.web.routes.revoke_session", fake_revoke_session)


class FakeCognitoTokens:
    def __init__(
        self,
        access_token: str = "access-token",
        id_token: str = "id-token",
        refresh_token: str = "refresh-token",
        expires_in: int = 3600,
    ) -> None:
        self.access_token = access_token
        self.id_token = id_token
        self.refresh_token = refresh_token
        self.expires_in = expires_in


class FakeCognitoUserInfo:
    def __init__(
        self,
        cognito_sub: str = "cog-sub-123",
        cognito_username: str = "user@example.com",
        email: str = "user@example.com",
        provider: str = "cognito",
        provider_user_id: str = "cognito-uid-123",
    ) -> None:
        self.cognito_sub = cognito_sub
        self.cognito_username = cognito_username
        self.email = email
        self.provider = provider
        self.provider_user_id = provider_user_id


class FakeTokenExchanger:
    def __init__(self, tokens: FakeCognitoTokens | None = None) -> None:
        self.tokens = tokens or FakeCognitoTokens()
        self.calls: list[tuple[str, str]] = []

    async def exchange_code(self, code: str, redirect_uri: str) -> FakeCognitoTokens:
        self.calls.append((code, redirect_uri))
        return self.tokens


class FakeUserInfoFetcher:
    def __init__(self, user_info: FakeCognitoUserInfo | None = None) -> None:
        self.user_info = user_info or FakeCognitoUserInfo()
        self.calls: list[str] = []

    async def get_user_info(self, id_token: str) -> FakeCognitoUserInfo:
        self.calls.append(id_token)
        return self.user_info


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class TestMakeWebRouter:
    def test_login_redirects_to_cognito(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET /auth/login redirects to Cognito authorize URL."""
        pool = MagicMock()
        audit = MagicMock()

        router = make_web_router(
            cognito_authorize_base_url="https://cognito.example.com/oauth2/authorize",
            cognito_client_id="test-client-id",
            callback_url="https://myapp.example.com/auth/callback",
            token_exchanger=FakeTokenExchanger(),
            user_info_fetcher=FakeUserInfoFetcher(),
            pool=pool,
            audit=audit,
        )

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/auth/login", follow_redirects=False)
        assert response.status_code == 302
        assert "https://cognito.example.com/oauth2/authorize" in response.headers["location"]
        assert "client_id=test-client-id" in response.headers["location"]
        assert "state=" in response.headers["location"]
        # Check that state cookie is set
        assert "auth_state" in client.cookies

    def test_callback_with_known_sub(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET /auth/callback with known cognito_sub creates session."""
        pool = MagicMock()
        conn = AsyncMock()
        _patch_system_tx(monkeypatch, conn)
        _patch_create_session(monkeypatch)

        tenant_id = uuid4()
        identity_id = uuid4()
        cognito_sub = "cog-sub-existing-user"

        # Mock fetchrow to return existing identity
        conn.fetchrow.return_value = {
            "tenant_id": tenant_id,
            "id": identity_id,
        }

        user_info = FakeCognitoUserInfo(cognito_sub=cognito_sub)
        token_exchanger = FakeTokenExchanger()
        user_info_fetcher = FakeUserInfoFetcher(user_info=user_info)

        router = make_web_router(
            cognito_authorize_base_url="https://cognito.example.com/oauth2/authorize",
            cognito_client_id="test-client-id",
            callback_url="https://myapp.example.com/auth/callback",
            token_exchanger=token_exchanger,
            user_info_fetcher=user_info_fetcher,
            pool=pool,
            audit=MagicMock(),
        )

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        # First, set the state cookie
        state_value = "test-state-123"
        client.cookies.set("auth_state", state_value)

        response = client.get(
            "/auth/callback",
            params={"code": "auth-code-123", "state": state_value},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert response.headers["location"] == "/dashboard"
        # Check that mem_session cookie is set
        assert "mem_session" in client.cookies

    def test_callback_with_unknown_sub(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET /auth/callback with unknown cognito_sub redirects to /welcome."""
        pool = MagicMock()
        conn = AsyncMock()
        _patch_system_tx(monkeypatch, conn)
        _patch_create_session(monkeypatch)

        # Mock fetchrow to return None (unknown user)
        conn.fetchrow.return_value = None

        user_info = FakeCognitoUserInfo(cognito_sub="cog-sub-new-user")
        token_exchanger = FakeTokenExchanger()
        user_info_fetcher = FakeUserInfoFetcher(user_info=user_info)

        router = make_web_router(
            cognito_authorize_base_url="https://cognito.example.com/oauth2/authorize",
            cognito_client_id="test-client-id",
            callback_url="https://myapp.example.com/auth/callback",
            token_exchanger=token_exchanger,
            user_info_fetcher=user_info_fetcher,
            pool=pool,
            audit=MagicMock(),
        )

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        # Set the state cookie
        state_value = "test-state-123"
        client.cookies.set("auth_state", state_value)

        response = client.get(
            "/auth/callback",
            params={"code": "auth-code-123", "state": state_value},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "tenant_not_provisioned" in response.headers["location"]

    def test_callback_with_invalid_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET /auth/callback with invalid state redirects to /welcome."""
        pool = MagicMock()
        audit = MagicMock()

        router = make_web_router(
            cognito_authorize_base_url="https://cognito.example.com/oauth2/authorize",
            cognito_client_id="test-client-id",
            callback_url="https://myapp.example.com/auth/callback",
            token_exchanger=FakeTokenExchanger(),
            user_info_fetcher=FakeUserInfoFetcher(),
            pool=pool,
            audit=audit,
        )

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        # Set a state cookie but send a different state in the query
        client.cookies.set("auth_state", "state-from-cookie")

        response = client.get(
            "/auth/callback",
            params={"code": "auth-code-123", "state": "state-from-cognito-mismatch"},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "invalid_state" in response.headers["location"]

    def test_callback_missing_state_cookie(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET /auth/callback without state cookie redirects to /welcome."""
        pool = MagicMock()
        audit = MagicMock()

        router = make_web_router(
            cognito_authorize_base_url="https://cognito.example.com/oauth2/authorize",
            cognito_client_id="test-client-id",
            callback_url="https://myapp.example.com/auth/callback",
            token_exchanger=FakeTokenExchanger(),
            user_info_fetcher=FakeUserInfoFetcher(),
            pool=pool,
            audit=audit,
        )

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        # Don't set auth_state cookie
        response = client.get(
            "/auth/callback",
            params={"code": "auth-code-123", "state": "some-state"},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "invalid_state" in response.headers["location"]

    def test_callback_token_exchange_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET /auth/callback with token exchange failure redirects to /welcome."""
        pool = MagicMock()
        audit = MagicMock()

        # Create a token exchanger that raises an exception
        async def failing_exchange_code(code: str, redirect_uri: str) -> None:
            raise Exception("Token exchange failed")

        fake_exchanger = MagicMock()
        fake_exchanger.exchange_code = failing_exchange_code

        router = make_web_router(
            cognito_authorize_base_url="https://cognito.example.com/oauth2/authorize",
            cognito_client_id="test-client-id",
            callback_url="https://myapp.example.com/auth/callback",
            token_exchanger=fake_exchanger,
            user_info_fetcher=FakeUserInfoFetcher(),
            pool=pool,
            audit=audit,
        )

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        state_value = "test-state-123"
        client.cookies.set("auth_state", state_value)

        response = client.get(
            "/auth/callback",
            params={"code": "auth-code-123", "state": state_value},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "token_exchange_failed" in response.headers["location"]

    def test_logout_revokes_and_clears(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /auth/logout revokes session and clears cookie."""
        pool = MagicMock()
        _patch_revoke_session(monkeypatch)

        router = make_web_router(
            cognito_authorize_base_url="https://cognito.example.com/oauth2/authorize",
            cognito_client_id="test-client-id",
            callback_url="https://myapp.example.com/auth/callback",
            token_exchanger=FakeTokenExchanger(),
            user_info_fetcher=FakeUserInfoFetcher(),
            pool=pool,
            audit=MagicMock(),
        )

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        # Set the mem_session cookie
        client.cookies.set("mem_session", "test-session-value")

        response = client.post("/auth/logout", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/"
        # Response should include Set-Cookie with Max-Age=0 to clear the cookie
        assert "Set-Cookie" in response.headers or "mem_session" not in client.cookies

    def test_logout_without_cookie(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /auth/logout without session cookie still redirects."""
        pool = MagicMock()
        _patch_revoke_session(monkeypatch)

        router = make_web_router(
            cognito_authorize_base_url="https://cognito.example.com/oauth2/authorize",
            cognito_client_id="test-client-id",
            callback_url="https://myapp.example.com/auth/callback",
            token_exchanger=FakeTokenExchanger(),
            user_info_fetcher=FakeUserInfoFetcher(),
            pool=pool,
            audit=MagicMock(),
        )

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        # Don't set mem_session cookie
        response = client.post("/auth/logout", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/"
