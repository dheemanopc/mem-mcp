"""Tests for memory CRUD handlers (T-8.6 + T-8.7)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mem_mcp.mcp.tools._deps import ToolDeps
from mem_mcp.mcp.tools.delete import MemoryDeleteOutput
from mem_mcp.mcp.tools.get import MemoryGetOutput, MemoryRecord
from mem_mcp.mcp.tools.list import MemoryListItem, MemoryListOutput
from mem_mcp.mcp.tools.undelete import MemoryUndeleteOutput
from mem_mcp.mcp.tools.update import MemoryUpdateOutput
from mem_mcp.web.handlers.memories import make_memories_router
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
async def test_list_memories_no_session_returns_401(mock_pool: Any, mock_deps: Any) -> None:
    """Missing session cookie returns 401."""
    router = make_memories_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/api/web/memories")
    assert response.status_code == 401
    assert response.json()["detail"] == "not authenticated"


@pytest.mark.asyncio
async def test_list_memories_invalid_session_returns_401(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_deps: Any
) -> None:
    """Invalid session cookie returns 401."""
    monkeypatch.setattr(
        "mem_mcp.web.handlers.memories.lookup_session",
        AsyncMock(return_value=None),
    )

    router = make_memories_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get(
        "/api/web/memories",
        cookies={"mem_session": "invalid"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_memories_happy_path(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_deps: Any
) -> None:
    """Happy path: valid session, returns list of memories."""
    tenant_id = uuid4()
    identity_id = uuid4()
    memory_id = uuid4()
    now = datetime.utcnow()

    monkeypatch.setattr(
        "mem_mcp.web.handlers.memories.lookup_session",
        AsyncMock(
            return_value=SessionContext(
                tenant_id=tenant_id,
                identity_id=identity_id,
            )
        ),
    )

    monkeypatch.setattr(
        "mem_mcp.web.handlers.memories.MemoryListTool.__call__",
        AsyncMock(
            return_value=MemoryListOutput(
                results=[
                    MemoryListItem(
                        id=memory_id,
                        content="Test memory",
                        type="note",
                        tags=["test"],
                        version=1,
                        is_current=True,
                        created_at=now,
                        updated_at=now,
                        deleted_at=None,
                    )
                ],
                next_cursor=None,
                request_id="test-req-id",
            )
        ),
    )

    router = make_memories_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get(
        "/api/web/memories",
        cookies={"mem_session": "valid"},
    )
    assert response.status_code == 200

    data = response.json()
    assert len(data["results"]) == 1
    assert data["results"][0]["content"] == "Test memory"
    assert data["results"][0]["type"] == "note"


@pytest.mark.asyncio
async def test_list_memories_with_filters(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_deps: Any
) -> None:
    """Query params for type and tag are passed to tool."""
    tenant_id = uuid4()
    identity_id = uuid4()
    captured_inp: dict[str, Any] = {}

    monkeypatch.setattr(
        "mem_mcp.web.handlers.memories.lookup_session",
        AsyncMock(
            return_value=SessionContext(
                tenant_id=tenant_id,
                identity_id=identity_id,
            )
        ),
    )

    async def capture_and_return(ctx: Any, inp: Any) -> MemoryListOutput:
        captured_inp["inp"] = inp
        return MemoryListOutput(results=[], next_cursor=None, request_id="test")

    monkeypatch.setattr(
        "mem_mcp.web.handlers.memories.MemoryListTool.__call__",
        AsyncMock(side_effect=capture_and_return),
    )

    router = make_memories_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get(
        "/api/web/memories?type=note&tag=foo&tag=bar",
        cookies={"mem_session": "valid"},
    )
    assert response.status_code == 200

    inp = captured_inp.get("inp")
    assert inp is not None
    assert inp.type == "note"
    assert inp.tags == ["foo", "bar"]


@pytest.mark.asyncio
async def test_get_memory_happy_path(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_deps: Any
) -> None:
    """GET /:id returns memory detail."""
    tenant_id = uuid4()
    identity_id = uuid4()
    memory_id = uuid4()
    now = datetime.utcnow()

    monkeypatch.setattr(
        "mem_mcp.web.handlers.memories.lookup_session",
        AsyncMock(
            return_value=SessionContext(
                tenant_id=tenant_id,
                identity_id=identity_id,
            )
        ),
    )

    monkeypatch.setattr(
        "mem_mcp.web.handlers.memories.MemoryGetTool.__call__",
        AsyncMock(
            return_value=MemoryGetOutput(
                memory=MemoryRecord(
                    id=memory_id,
                    content="Test content",
                    type="note",
                    tags=["test"],
                    metadata={},
                    version=1,
                    is_current=True,
                    supersedes=None,
                    superseded_by=None,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                ),
                history=[],
            )
        ),
    )

    router = make_memories_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get(
        f"/api/web/memories/{memory_id}",
        cookies={"mem_session": "valid"},
    )
    assert response.status_code == 200

    data = response.json()
    assert data["memory"]["content"] == "Test content"
    assert data["memory"]["type"] == "note"
    assert len(data["history"]) == 0


@pytest.mark.asyncio
async def test_get_memory_not_found_returns_404(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_deps: Any
) -> None:
    """GET /:id with invalid id returns 404."""
    from mem_mcp.mcp.errors import JsonRpcError

    tenant_id = uuid4()
    identity_id = uuid4()
    memory_id = uuid4()

    monkeypatch.setattr(
        "mem_mcp.web.handlers.memories.lookup_session",
        AsyncMock(
            return_value=SessionContext(
                tenant_id=tenant_id,
                identity_id=identity_id,
            )
        ),
    )

    monkeypatch.setattr(
        "mem_mcp.web.handlers.memories.MemoryGetTool.__call__",
        AsyncMock(
            side_effect=JsonRpcError(
                -32602,
                "memory not found",
                data={"errors": [{"path": "id", "message": "not found in tenant scope"}]},
            )
        ),
    )

    router = make_memories_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        f"/api/web/memories/{memory_id}",
        cookies={"mem_session": "valid"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_memory_happy_path(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_deps: Any
) -> None:
    """PATCH /:id updates memory."""
    tenant_id = uuid4()
    identity_id = uuid4()
    memory_id = uuid4()

    monkeypatch.setattr(
        "mem_mcp.web.handlers.memories.lookup_session",
        AsyncMock(
            return_value=SessionContext(
                tenant_id=tenant_id,
                identity_id=identity_id,
            )
        ),
    )

    monkeypatch.setattr(
        "mem_mcp.web.handlers.memories.MemoryUpdateTool.__call__",
        AsyncMock(
            return_value=MemoryUpdateOutput(
                id=memory_id,
                version=1,
                is_new_version=False,
                tags=["updated"],
                request_id="test",
            )
        ),
    )

    router = make_memories_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.patch(
        f"/api/web/memories/{memory_id}",
        json={"tags": ["updated"]},
        cookies={"mem_session": "valid"},
    )
    assert response.status_code == 200

    data = response.json()
    assert data["id"] == str(memory_id)
    assert data["tags"] == ["updated"]


@pytest.mark.asyncio
async def test_delete_memory_happy_path(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_deps: Any
) -> None:
    """DELETE /:id soft-deletes memory."""
    tenant_id = uuid4()
    identity_id = uuid4()
    memory_id = uuid4()
    now = datetime.utcnow()

    monkeypatch.setattr(
        "mem_mcp.web.handlers.memories.lookup_session",
        AsyncMock(
            return_value=SessionContext(
                tenant_id=tenant_id,
                identity_id=identity_id,
            )
        ),
    )

    monkeypatch.setattr(
        "mem_mcp.web.handlers.memories.MemoryDeleteTool.__call__",
        AsyncMock(
            return_value=MemoryDeleteOutput(
                id=memory_id,
                deleted_at=now,
                promoted_version_id=None,
                cascaded_count=0,
                request_id="test",
            )
        ),
    )

    router = make_memories_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.delete(
        f"/api/web/memories/{memory_id}",
        cookies={"mem_session": "valid"},
    )
    assert response.status_code == 200

    data = response.json()
    assert data["id"] == str(memory_id)
    assert data["deleted_at"] is not None


@pytest.mark.asyncio
async def test_undelete_memory_happy_path(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_deps: Any
) -> None:
    """POST /:id/undelete restores deleted memory."""
    tenant_id = uuid4()
    identity_id = uuid4()
    memory_id = uuid4()

    monkeypatch.setattr(
        "mem_mcp.web.handlers.memories.lookup_session",
        AsyncMock(
            return_value=SessionContext(
                tenant_id=tenant_id,
                identity_id=identity_id,
            )
        ),
    )

    monkeypatch.setattr(
        "mem_mcp.web.handlers.memories.MemoryUndeleteTool.__call__",
        AsyncMock(
            return_value=MemoryUndeleteOutput(
                id=memory_id,
                deleted_at=None,
                is_current=True,
                request_id="test",
            )
        ),
    )

    router = make_memories_router(pool=mock_pool, deps=mock_deps)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        f"/api/web/memories/{memory_id}/undelete",
        cookies={"mem_session": "valid"},
    )
    assert response.status_code == 200

    data = response.json()
    assert data["id"] == str(memory_id)
    assert data["deleted_at"] is None
    assert data["is_current"] is True
