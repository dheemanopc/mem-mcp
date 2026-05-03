"""Tests for OAuth client management handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mem_mcp.web.handlers.clients import make_clients_router


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
def mock_client_deleter() -> MagicMock:
    """Mock CognitoClientDeleter."""
    deleter = MagicMock()
    deleter.delete_user_pool_client = AsyncMock()
    return deleter


@pytest.fixture
def app(mock_pool: MagicMock, mock_audit: MagicMock, mock_client_deleter: MagicMock) -> FastAPI:
    """Create FastAPI app with clients router."""
    app = FastAPI()
    router = make_clients_router(
        pool=mock_pool,
        audit=mock_audit,
        client_deleter=mock_client_deleter,
    )
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """FastAPI test client."""
    return TestClient(app)


def test_list_clients(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/web/clients returns list of OAuth clients."""
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
    async def mock_system_tx(pool: object):  # type: ignore
        conn = MagicMock()
        conn.fetch = AsyncMock(
            return_value=[
                {
                    "id": "client123",
                    "client_name": "My App",
                    "software_id": "app-v1",
                    "review_status": "approved",
                    "disabled": False,
                    "created_at": "2026-05-01T00:00:00Z",
                    "last_used_at": "2026-05-03T00:00:00Z",
                }
            ]
        )
        yield conn

    monkeypatch.setattr("mem_mcp.web.handlers.clients.lookup_session", mock_lookup)
    monkeypatch.setattr("mem_mcp.web.handlers.clients.system_tx", mock_system_tx)

    response = client.get(
        "/api/web/clients",
        cookies={"mem_session": "fake_token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["client_name"] == "My App"
    assert data[0]["disabled"] is False


def test_revoke_happy(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE /api/web/clients/{id} succeeds."""
    from mem_mcp.web import sessions

    tenant_id = uuid4()
    identity_id = uuid4()

    mock_session = sessions.SessionContext(tenant_id=tenant_id, identity_id=identity_id)

    async def mock_lookup(
        pool: object, raw_value: str, now: object = None
    ) -> sessions.SessionContext:
        return mock_session

    async def mock_revoke_client(pool: object, **kwargs: object) -> None:
        pass

    monkeypatch.setattr("mem_mcp.web.handlers.clients.lookup_session", mock_lookup)
    monkeypatch.setattr("mem_mcp.web.handlers.clients.revoke_client", mock_revoke_client)

    response = client.delete(
        "/api/web/clients/client123",
        cookies={"mem_session": "fake_token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True


def test_revoke_not_found_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE /api/web/clients/{id} with non-existent client → 404."""
    from mem_mcp.oauth import revoke
    from mem_mcp.web import sessions

    tenant_id = uuid4()
    identity_id = uuid4()

    mock_session = sessions.SessionContext(tenant_id=tenant_id, identity_id=identity_id)

    async def mock_lookup(
        pool: object, raw_value: str, now: object = None
    ) -> sessions.SessionContext:
        return mock_session

    async def mock_revoke_client(pool: object, **kwargs: object) -> None:
        raise revoke.RevokeClientError("not_found")

    monkeypatch.setattr("mem_mcp.web.handlers.clients.lookup_session", mock_lookup)
    monkeypatch.setattr("mem_mcp.web.handlers.clients.revoke_client", mock_revoke_client)

    response = client.delete(
        "/api/web/clients/nonexistent",
        cookies={"mem_session": "fake_token"},
    )
    assert response.status_code == 404
