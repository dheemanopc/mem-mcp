"""Tests for GET /api/web/stats handler (T-8.5)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mem_mcp.mcp.tools._deps import ToolDeps
from mem_mcp.mcp.tools.stats import MemoryStatsOutput, QuotaConfig, TodayUsage
from mem_mcp.web.handlers.stats import make_stats_router
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
async def test_get_stats_no_session_returns_401(mock_pool: Any, mock_deps: Any) -> None:
    """Missing session cookie returns 401."""
    router = make_stats_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/api/web/stats")
    assert response.status_code == 401
    assert response.json()["detail"] == "not authenticated"


@pytest.mark.asyncio
async def test_get_stats_invalid_session_returns_401(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_deps: Any
) -> None:
    """Invalid session cookie returns 401."""
    monkeypatch.setattr(
        "mem_mcp.web.handlers.stats.lookup_session",
        AsyncMock(return_value=None),
    )

    router = make_stats_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/api/web/stats", cookies={"mem_session": "invalid"})
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid session"


@pytest.mark.asyncio
async def test_get_stats_happy_path(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_deps: Any
) -> None:
    """Happy path: valid session, returns stats JSON."""
    tenant_id = uuid4()
    identity_id = uuid4()

    monkeypatch.setattr(
        "mem_mcp.web.handlers.stats.lookup_session",
        AsyncMock(
            return_value=SessionContext(
                tenant_id=tenant_id,
                identity_id=identity_id,
            )
        ),
    )

    monkeypatch.setattr(
        "mem_mcp.web.handlers.stats.MemoryStatsTool.__call__",
        AsyncMock(
            return_value=MemoryStatsOutput(
                total_memories=5,
                by_type={"work": 3, "personal": 2},
                top_tags=[],
                oldest=None,
                newest=None,
                today=TodayUsage(writes=1, reads=2, embed_tokens=100),
                quota=QuotaConfig(
                    tier="starter",
                    memories_limit=1000,
                    embed_tokens_daily_limit=50000,
                    writes_per_minute_limit=10,
                    reads_per_minute_limit=30,
                ),
                request_id="test-req-id",
            )
        ),
    )

    router = make_stats_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/api/web/stats", cookies={"mem_session": "valid"})
    assert response.status_code == 200

    data = response.json()
    assert data["total_memories"] == 5
    assert data["by_type"] == {"work": 3, "personal": 2}
    assert data["today"]["writes"] == 1
    assert data["quota"]["tier"] == "starter"


@pytest.mark.asyncio
async def test_get_stats_uses_correct_scopes(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_deps: Any
) -> None:
    """Verify ToolContext is built with memory.read scope."""
    tenant_id = uuid4()
    identity_id = uuid4()
    captured_ctx: dict[str, Any] = {}

    monkeypatch.setattr(
        "mem_mcp.web.handlers.stats.lookup_session",
        AsyncMock(
            return_value=SessionContext(
                tenant_id=tenant_id,
                identity_id=identity_id,
            )
        ),
    )

    async def capture_and_return(ctx: Any, inp: Any) -> MemoryStatsOutput:
        captured_ctx["ctx"] = ctx
        return MemoryStatsOutput(
            total_memories=0,
            by_type={},
            top_tags=[],
            oldest=None,
            newest=None,
            today=TodayUsage(writes=0, reads=0, embed_tokens=0),
            quota=QuotaConfig(
                tier="starter",
                memories_limit=1000,
                embed_tokens_daily_limit=50000,
                writes_per_minute_limit=10,
                reads_per_minute_limit=30,
            ),
            request_id="test",
        )

    monkeypatch.setattr(
        "mem_mcp.web.handlers.stats.MemoryStatsTool.__call__",
        AsyncMock(side_effect=capture_and_return),
    )

    router = make_stats_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/api/web/stats", cookies={"mem_session": "valid"})
    assert response.status_code == 200

    ctx = captured_ctx.get("ctx")
    assert ctx is not None
    assert ctx.tenant_id == tenant_id
    assert ctx.identity_id == identity_id
    assert "memory.read" in ctx.scopes
    assert ctx.client_id == "web"
