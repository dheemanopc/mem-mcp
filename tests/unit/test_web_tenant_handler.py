"""Tests for tenant settings handlers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mem_mcp.web.handlers.tenant import make_tenant_router


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
def mock_sign_outter() -> MagicMock:
    """Mock CognitoGlobalSignOutter."""
    sign_outter = MagicMock()
    sign_outter.admin_user_global_sign_out = AsyncMock()
    return sign_outter


@pytest.fixture
def app(mock_pool: MagicMock, mock_audit: MagicMock, mock_sign_outter: MagicMock) -> FastAPI:
    """Create FastAPI app with tenant router."""
    app = FastAPI()
    router = make_tenant_router(
        pool=mock_pool,
        audit=mock_audit,
        sign_outter=mock_sign_outter,
    )
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """FastAPI test client."""
    return TestClient(app)


def test_get_me_no_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/web/me without session → 401."""
    monkeypatch.setattr(
        "mem_mcp.web.handlers.tenant.lookup_session",
        AsyncMock(return_value=None),
    )
    response = client.get("/api/web/me")
    assert response.status_code == 401


def test_get_me_happy(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/web/me with valid session → returns tenant info."""
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
        conn.fetchrow = AsyncMock(
            return_value={
                "email": "user@example.com",
                "status": "active",
                "tier": "pro",
                "retention_days": 90,
            }
        )
        yield conn

    monkeypatch.setattr("mem_mcp.web.handlers.tenant.lookup_session", mock_lookup)
    monkeypatch.setattr("mem_mcp.web.handlers.tenant.system_tx", mock_system_tx)

    response = client.get("/api/web/me", cookies={"mem_session": "fake_token"})
    assert response.status_code == 200
    data = response.json()
    assert data["tenant_id"] == str(tenant_id)
    assert data["identity_id"] == str(identity_id)
    assert data["tenant"]["email"] == "user@example.com"
    assert data["tenant"]["retention_days"] == 90


def test_patch_tenant_validates_retention_range(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PATCH /api/web/tenant validates retention_days in [7, 3650]."""
    from contextlib import asynccontextmanager

    from mem_mcp.web import sessions

    tenant_id = uuid4()
    identity_id = uuid4()

    mock_session = sessions.SessionContext(tenant_id=tenant_id, identity_id=identity_id)

    async def mock_lookup(
        pool: object, raw_value: str, now: object = None
    ) -> sessions.SessionContext:
        return mock_session

    monkeypatch.setattr("mem_mcp.web.handlers.tenant.lookup_session", mock_lookup)

    # Test out-of-range (too low)
    response = client.patch(
        "/api/web/tenant",
        json={"retention_days": 3},
        cookies={"mem_session": "fake_token"},
    )
    assert response.status_code == 422  # Validation error

    # Test out-of-range (too high)
    response = client.patch(
        "/api/web/tenant",
        json={"retention_days": 4000},
        cookies={"mem_session": "fake_token"},
    )
    assert response.status_code == 422

    # Valid range
    @asynccontextmanager
    async def mock_system_tx(pool: object):  # type: ignore
        conn = MagicMock()
        conn.execute = AsyncMock()
        yield conn

    monkeypatch.setattr("mem_mcp.web.handlers.tenant.system_tx", mock_system_tx)

    response = client.patch(
        "/api/web/tenant",
        json={"retention_days": 90},
        cookies={"mem_session": "fake_token"},
    )
    assert response.status_code == 200


def test_request_delete_calls_request_closure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/web/data/delete calls request_closure and returns result."""
    from mem_mcp.identity import lifecycle
    from mem_mcp.web import sessions

    tenant_id = uuid4()
    identity_id = uuid4()

    mock_session = sessions.SessionContext(tenant_id=tenant_id, identity_id=identity_id)

    async def mock_lookup(
        pool: object, raw_value: str, now: object = None
    ) -> sessions.SessionContext:
        return mock_session

    monkeypatch.setattr("mem_mcp.web.handlers.tenant.lookup_session", mock_lookup)

    # Mock request_closure
    async def mock_request_closure(
        pool: object, **kwargs: object
    ) -> lifecycle.ClosureRequestResult:
        return lifecycle.ClosureRequestResult(
            cancel_token="token123",
            cancel_until=datetime.now(tz=UTC) + timedelta(hours=24),
            identities_signed_out=2,
        )

    monkeypatch.setattr("mem_mcp.web.handlers.tenant.request_closure", mock_request_closure)

    response = client.post(
        "/api/web/data/delete",
        cookies={"mem_session": "fake_token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "cancel_until" in data
    assert data["identities_signed_out"] == 2


def test_cancel_delete_with_invalid_token_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/web/data/delete/cancel with invalid token → 400."""
    from mem_mcp.identity import lifecycle
    from mem_mcp.web import sessions

    tenant_id = uuid4()
    identity_id = uuid4()

    mock_session = sessions.SessionContext(tenant_id=tenant_id, identity_id=identity_id)

    async def mock_lookup(
        pool: object, raw_value: str, now: object = None
    ) -> sessions.SessionContext:
        return mock_session

    monkeypatch.setattr("mem_mcp.web.handlers.tenant.lookup_session", mock_lookup)

    # Mock cancel_closure to raise error
    async def mock_cancel_closure(pool: object, **kwargs: object) -> None:
        raise lifecycle.ClosureError("token_invalid", detail="Invalid token")

    monkeypatch.setattr("mem_mcp.web.handlers.tenant.cancel_closure", mock_cancel_closure)

    response = client.post(
        "/api/web/data/delete/cancel",
        json={"cancel_token": "badtoken"},
        cookies={"mem_session": "fake_token"},
    )
    assert response.status_code == 400
