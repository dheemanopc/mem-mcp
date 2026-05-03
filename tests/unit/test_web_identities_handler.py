"""Tests for identity management handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mem_mcp.web.handlers.identities import make_identities_router


@pytest.fixture
def mock_pool() -> MagicMock:
    """Mock asyncpg.Pool."""
    return MagicMock()


@pytest.fixture
def mock_audit() -> MagicMock:
    """Mock AuditLogger."""
    audit = MagicMock()
    audit.audit = AsyncMock()
    return audit


@pytest.fixture
def mock_admin_deleter() -> MagicMock:
    """Mock CognitoAdminDeleter."""
    deleter = MagicMock()
    deleter.admin_delete_user = AsyncMock()
    return deleter


@pytest.fixture
def app(mock_pool: MagicMock, mock_audit: MagicMock, mock_admin_deleter: MagicMock) -> FastAPI:
    """Create FastAPI app with identities router."""
    app = FastAPI()
    router = make_identities_router(
        pool=mock_pool,
        audit=mock_audit,
        admin_deleter=mock_admin_deleter,
        hmac_secret="test_secret",
        cognito_authorize_base_url="https://auth.example.com/oauth2/authorize",
        cognito_client_id="test_client",
        callback_url="http://localhost:3000/auth/callback",
    )
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """FastAPI test client."""
    return TestClient(app)


def test_list_identities(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/web/identities returns list of identities."""
    from contextlib import asynccontextmanager

    from mem_mcp.web import sessions

    tenant_id = uuid4()
    identity_id = uuid4()

    mock_session = sessions.SessionContext(tenant_id=tenant_id, identity_id=identity_id)

    async def mock_lookup(
        pool: object, raw_value: str, now: object = None
    ) -> sessions.SessionContext:
        return mock_session

    @asynccontextmanager
    async def mock_tenant_tx(pool: object, tid: UUID):  # type: ignore
        conn = MagicMock()
        conn.fetch = AsyncMock(
            return_value=[
                {
                    "id": identity_id,
                    "email": "user@example.com",
                    "provider": "cognito",
                    "is_primary": True,
                    "linked_at": "2026-05-01T00:00:00Z",
                    "last_seen_at": "2026-05-03T00:00:00Z",
                }
            ]
        )
        yield conn

    monkeypatch.setattr("mem_mcp.web.handlers.identities.lookup_session", mock_lookup)
    monkeypatch.setattr("mem_mcp.web.handlers.identities.tenant_tx", mock_tenant_tx)

    response = client.get(
        "/api/web/identities",
        cookies={"mem_session": "fake_token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["email"] == "user@example.com"
    assert data[0]["is_primary"] is True


def test_link_start_returns_url_and_sets_cookie(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/web/identities/link/start returns authorize_url and sets cookie."""
    from mem_mcp.identity import linking
    from mem_mcp.web import sessions

    tenant_id = uuid4()
    identity_id = uuid4()

    mock_session = sessions.SessionContext(tenant_id=tenant_id, identity_id=identity_id)

    async def mock_lookup(
        pool: object, raw_value: str, now: object = None
    ) -> sessions.SessionContext:
        return mock_session

    async def mock_start_link(pool: object, **kwargs: object) -> linking.StartLinkResult:
        return linking.StartLinkResult(
            authorize_url="https://auth.example.com/oauth2/authorize?state=sig",
            signed_state="sig",
            nonce="nonce123",
        )

    monkeypatch.setattr("mem_mcp.web.handlers.identities.lookup_session", mock_lookup)
    monkeypatch.setattr("mem_mcp.web.handlers.identities.start_link", mock_start_link)

    response = client.post(
        "/api/web/identities/link/start",
        cookies={"mem_session": "fake_token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "authorize_url" in data
    assert "link_state_cookie" in response.cookies


def test_unlink_last_identity_400(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE /api/web/identities/{id} with last identity → 400."""
    from mem_mcp.identity import unlinking
    from mem_mcp.web import sessions

    tenant_id = uuid4()
    identity_id = uuid4()

    mock_session = sessions.SessionContext(tenant_id=tenant_id, identity_id=identity_id)

    async def mock_lookup(
        pool: object, raw_value: str, now: object = None
    ) -> sessions.SessionContext:
        return mock_session

    async def mock_unlink_identity(pool: object, **kwargs: object) -> None:
        raise unlinking.UnlinkingError("last_identity", "Cannot unlink the only identity")

    monkeypatch.setattr("mem_mcp.web.handlers.identities.lookup_session", mock_lookup)
    monkeypatch.setattr("mem_mcp.web.handlers.identities.unlink_identity", mock_unlink_identity)

    response = client.delete(
        f"/api/web/identities/{identity_id}",
        cookies={"mem_session": "fake_token"},
    )
    assert response.status_code == 400


def test_promote_happy(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/web/identities/{id}/promote succeeds."""
    from mem_mcp.web import sessions

    tenant_id = uuid4()
    identity_id = uuid4()

    mock_session = sessions.SessionContext(tenant_id=tenant_id, identity_id=identity_id)

    async def mock_lookup(
        pool: object, raw_value: str, now: object = None
    ) -> sessions.SessionContext:
        return mock_session

    async def mock_promote_primary(pool: object, **kwargs: object) -> None:
        pass

    monkeypatch.setattr("mem_mcp.web.handlers.identities.lookup_session", mock_lookup)
    monkeypatch.setattr("mem_mcp.web.handlers.identities.promote_primary", mock_promote_primary)

    response = client.post(
        f"/api/web/identities/{identity_id}/promote",
        cookies={"mem_session": "fake_token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
