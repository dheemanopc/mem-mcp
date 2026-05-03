"""Tests for web export handler (T-8.12)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mem_mcp.mcp.tools.export import MemoryExportOutput
from mem_mcp.web.handlers.export import make_export_router
from mem_mcp.web.sessions import SessionContext


@pytest.fixture
def mock_pool() -> MagicMock:
    """Mock asyncpg.Pool."""
    return MagicMock()


@pytest.fixture
def mock_deps() -> MagicMock:
    """Mock ToolDeps."""
    return MagicMock()


@pytest.fixture
def app(mock_pool: MagicMock, mock_deps: MagicMock) -> FastAPI:
    """Create FastAPI app with export router."""
    app = FastAPI()
    router = make_export_router(pool=mock_pool, deps=mock_deps)
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """FastAPI test client."""
    return TestClient(app)


def test_export_no_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/web/data/export without session → 401."""
    monkeypatch.setattr(
        "mem_mcp.web.handlers.export.lookup_session",
        AsyncMock(return_value=None),
    )
    response = client.get("/api/web/data/export")
    assert response.status_code == 401


def test_export_invalid_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/web/data/export with invalid session → 401."""
    monkeypatch.setattr(
        "mem_mcp.web.handlers.export.lookup_session",
        AsyncMock(return_value=None),
    )
    response = client.get("/api/web/data/export", cookies={"mem_session": "invalid"})
    assert response.status_code == 401


def test_export_happy_path_returns_attachment_header(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/web/data/export with valid session → returns JSON with Content-Disposition."""
    tenant_id = uuid4()
    identity_id = uuid4()
    mock_session = SessionContext(tenant_id=tenant_id, identity_id=identity_id)

    async def mock_lookup(pool: object, raw_value: str, now: object = None) -> SessionContext:
        return mock_session

    monkeypatch.setattr("mem_mcp.web.handlers.export.lookup_session", mock_lookup)

    # Mock MemoryExportTool call
    mock_export_output = MemoryExportOutput(
        exported_at=datetime.now(tz=UTC),
        memories=[{"id": "mem-1", "content": "test"}],
        audit_log=[{"id": "audit-1", "action": "create"}],
        request_id="req-123",
    )

    mock_tool_instance = AsyncMock(return_value=mock_export_output)
    mock_tool_class = MagicMock(return_value=mock_tool_instance)

    monkeypatch.setattr(
        "mem_mcp.web.handlers.export.MemoryExportTool",
        mock_tool_class,
    )

    response = client.get("/api/web/data/export", cookies={"mem_session": "test_session"})
    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "")
    assert "mem-mcp-export" in response.headers.get("content-disposition", "")
    assert str(tenant_id) in response.headers.get("content-disposition", "")


def test_export_includes_memories_and_audit(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/web/data/export returns memories and audit_log."""
    tenant_id = uuid4()
    identity_id = uuid4()
    mock_session = SessionContext(tenant_id=tenant_id, identity_id=identity_id)

    async def mock_lookup(pool: object, raw_value: str, now: object = None) -> SessionContext:
        return mock_session

    monkeypatch.setattr("mem_mcp.web.handlers.export.lookup_session", mock_lookup)

    memories = [
        {"id": "mem-1", "content": "memory 1", "version": 1},
        {"id": "mem-2", "content": "memory 2", "version": 2},
    ]
    audit = [
        {"id": "audit-1", "action": "create", "result": "success"},
        {"id": "audit-2", "action": "update", "result": "success"},
    ]

    mock_export_output = MemoryExportOutput(
        exported_at=datetime.now(tz=UTC),
        memories=memories,
        audit_log=audit,
        request_id="req-456",
    )

    mock_tool_instance = AsyncMock(return_value=mock_export_output)
    mock_tool_class = MagicMock(return_value=mock_tool_instance)

    monkeypatch.setattr(
        "mem_mcp.web.handlers.export.MemoryExportTool",
        mock_tool_class,
    )

    response = client.get("/api/web/data/export", cookies={"mem_session": "test_session"})
    assert response.status_code == 200
    data = response.json()
    assert len(data["memories"]) == 2
    assert len(data["audit_log"]) == 2
    assert data["memories"][0]["id"] == "mem-1"
    assert data["audit_log"][0]["action"] == "create"
