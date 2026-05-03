"""Tests for memory.feedback tool (T-7.8)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mem_mcp.audit.logger import NoopAuditLogger
from mem_mcp.embeddings.bedrock import EmbedResult
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._deps import NoopQuotas, ToolDeps
from mem_mcp.mcp.tools.feedback import (
    MemoryFeedbackInput,
    MemoryFeedbackOutput,
    MemoryFeedbackTool,
)


class _StubEmbeddings:
    """Stub embeddings client; feedback doesn't embed but ToolDeps requires one."""

    async def embed(self, text: str) -> EmbedResult:
        raise RuntimeError("feedback should never embed")


def _patch_tenant_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    """Patch tenant_tx to yield our fake conn."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_tx(pool: Any, tenant_id: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.mcp.tools.feedback.tenant_tx", fake_tx)


def _build_ctx(scopes: tuple[str, ...] = ("memory.write",)) -> ToolContext:
    """Build a ToolContext with proper dependencies."""
    deps = ToolDeps(
        embeddings=_StubEmbeddings(),
        audit=NoopAuditLogger(),
        quotas=NoopQuotas(),
    )
    return ToolContext(
        request_id=str(uuid4()),
        tenant_id=uuid4(),
        identity_id=uuid4(),
        client_id="client-1",
        scopes=frozenset(scopes),
        db_pool=MagicMock(),
        deps=deps,
    )


class TestMemoryFeedback:
    """Tests for memory.feedback tool."""

    @pytest.mark.asyncio
    async def test_feedback_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Input with text + metadata; fetchrow returns id and created_at."""
        tool = MemoryFeedbackTool()
        ctx = _build_ctx()
        inp = MemoryFeedbackInput(text="Great feature!", metadata={"rating": 5})

        feedback_id = uuid4()
        now = datetime.now(tz=UTC)
        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": feedback_id,
            "created_at": now,
        }
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert isinstance(output, MemoryFeedbackOutput)
        assert output.id == feedback_id
        assert output.received_at == now
        assert output.request_id == ctx.request_id

    @pytest.mark.asyncio
    async def test_feedback_text_too_long(self) -> None:
        """Text > 4096 chars raises ValidationError."""
        with pytest.raises(ValidationError):
            MemoryFeedbackInput(text="x" * 4097)

    @pytest.mark.asyncio
    async def test_feedback_text_empty(self) -> None:
        """Text empty string raises ValidationError."""
        with pytest.raises(ValidationError):
            MemoryFeedbackInput(text="")

    @pytest.mark.asyncio
    async def test_feedback_default_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Input without metadata defaults to {} and INSERT still works."""
        tool = MemoryFeedbackTool()
        ctx = _build_ctx()
        inp = MemoryFeedbackInput(text="Good job!")

        feedback_id = uuid4()
        now = datetime.now(tz=UTC)
        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": feedback_id,
            "created_at": now,
        }
        _patch_tenant_tx(monkeypatch, conn)

        output = await tool(ctx, inp)
        assert isinstance(output, MemoryFeedbackOutput)
        assert output.id == feedback_id
