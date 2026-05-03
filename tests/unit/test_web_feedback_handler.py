"""Tests for POST /api/web/feedback handler (T-8.11)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mem_mcp.mcp.tools._deps import ToolDeps
from mem_mcp.mcp.tools.feedback import MemoryFeedbackOutput
from mem_mcp.web.handlers.feedback import make_feedback_router
from mem_mcp.web.sessions import SessionContext


@pytest.fixture
def mock_pool() -> Any:
    """Mock asyncpg.Pool."""
    return MagicMock()


@pytest.fixture
def mock_deps() -> Any:
    """Mock ToolDeps."""
    return MagicMock(spec=ToolDeps)


@pytest.mark.asyncio
async def test_post_feedback_no_session_returns_401(mock_pool: Any, mock_deps: Any) -> None:
    """Missing session cookie returns 401."""
    router = make_feedback_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post("/api/web/feedback", json={"text": "Test feedback"})
    assert response.status_code == 401
    assert response.json()["detail"] == "not authenticated"


@pytest.mark.asyncio
async def test_post_feedback_invalid_session_returns_401(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_deps: Any
) -> None:
    """Invalid session cookie returns 401."""
    monkeypatch.setattr(
        "mem_mcp.web.handlers.feedback.lookup_session",
        AsyncMock(return_value=None),
    )

    router = make_feedback_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/api/web/feedback",
        json={"text": "Test feedback"},
        cookies={"mem_session": "invalid"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_post_feedback_validates_text_empty_returns_422(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_deps: Any
) -> None:
    """Empty text fails Pydantic validation with 422."""
    monkeypatch.setattr(
        "mem_mcp.web.handlers.feedback.lookup_session",
        AsyncMock(
            return_value=SessionContext(
                tenant_id=uuid4(),
                identity_id=uuid4(),
            )
        ),
    )

    router = make_feedback_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/web/feedback",
        json={"text": ""},
        cookies={"mem_session": "valid"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_feedback_validates_text_too_long_returns_422(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_deps: Any
) -> None:
    """Text > 4096 chars fails validation with 422."""
    monkeypatch.setattr(
        "mem_mcp.web.handlers.feedback.lookup_session",
        AsyncMock(
            return_value=SessionContext(
                tenant_id=uuid4(),
                identity_id=uuid4(),
            )
        ),
    )

    router = make_feedback_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)

    long_text = "x" * 4097
    response = client.post(
        "/api/web/feedback",
        json={"text": long_text},
        cookies={"mem_session": "valid"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_feedback_happy_path(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_deps: Any
) -> None:
    """Happy path: valid session + text, returns id + received_at."""
    tenant_id = uuid4()
    identity_id = uuid4()
    feedback_id = uuid4()
    received_at = datetime.utcnow()

    monkeypatch.setattr(
        "mem_mcp.web.handlers.feedback.lookup_session",
        AsyncMock(
            return_value=SessionContext(
                tenant_id=tenant_id,
                identity_id=identity_id,
            )
        ),
    )

    monkeypatch.setattr(
        "mem_mcp.web.handlers.feedback.MemoryFeedbackTool.__call__",
        AsyncMock(
            return_value=MemoryFeedbackOutput(
                id=feedback_id,
                received_at=received_at,
                request_id="test-req-id",
            )
        ),
    )

    router = make_feedback_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/api/web/feedback",
        json={"text": "Great product!", "metadata": {"source": "dashboard"}},
        cookies={"mem_session": "valid"},
    )
    assert response.status_code == 200

    data = response.json()
    assert str(feedback_id) == data["id"]
    assert data["received_at"] is not None


@pytest.mark.asyncio
async def test_post_feedback_uses_correct_scopes(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_deps: Any
) -> None:
    """Verify ToolContext is built with memory.write scope."""
    tenant_id = uuid4()
    identity_id = uuid4()
    captured_ctx: dict[str, Any] = {}
    feedback_id = uuid4()

    monkeypatch.setattr(
        "mem_mcp.web.handlers.feedback.lookup_session",
        AsyncMock(
            return_value=SessionContext(
                tenant_id=tenant_id,
                identity_id=identity_id,
            )
        ),
    )

    async def capture_and_return(ctx: Any, inp: Any) -> MemoryFeedbackOutput:
        captured_ctx["ctx"] = ctx
        return MemoryFeedbackOutput(
            id=feedback_id,
            received_at=datetime.utcnow(),
            request_id="test",
        )

    monkeypatch.setattr(
        "mem_mcp.web.handlers.feedback.MemoryFeedbackTool.__call__",
        AsyncMock(side_effect=capture_and_return),
    )

    router = make_feedback_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/api/web/feedback",
        json={"text": "Test feedback"},
        cookies={"mem_session": "valid"},
    )
    assert response.status_code == 200

    ctx = captured_ctx.get("ctx")
    assert ctx is not None
    assert ctx.tenant_id == tenant_id
    assert ctx.identity_id == identity_id
    assert "memory.write" in ctx.scopes
    assert ctx.client_id == "web"
