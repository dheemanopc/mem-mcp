"""Integration tests for contradiction detection on memory_write.

DB-layer tests use the pg_pool fixture (skipped when MEM_MCP_TEST_DSN unset).
Tool-layer tests mock the DB and settings — no live DB required.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from mem_mcp.audit.logger import NoopAuditLogger
from mem_mcp.config import _reset_settings_cache_for_tests
from mem_mcp.embeddings.bedrock import EmbedResult
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools._deps import NoopQuotas, ToolDeps
from mem_mcp.mcp.tools.write import MemoryWriteInput, MemoryWriteTool
from mem_mcp.memory.contradiction import find_contradiction_candidates


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _StubEmbeddings:
    async def embed(self, text: str) -> EmbedResult:
        return EmbedResult(vector=[0.1] * 1024, input_tokens=5)


def _build_ctx(pool: Any = None) -> ToolContext:
    deps = ToolDeps(
        embeddings=_StubEmbeddings(),
        audit=NoopAuditLogger(),
        quotas=NoopQuotas(),
    )
    return ToolContext(
        request_id=str(uuid4()),
        tenant_id=uuid4(),
        identity_id=uuid4(),
        client_id="test-client",
        scopes=frozenset({"memory.write"}),
        db_pool=pool or MagicMock(),
        deps=deps,
    )


def _patch_tenant_tx(monkeypatch: pytest.MonkeyPatch, conn: AsyncMock) -> None:
    @asynccontextmanager
    async def _fake_tx(pool: Any, tenant_id: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.mcp.tools.write.tenant_tx", _fake_tx)


def _make_conn_for_plain_insert(team_id: Any, memory_id: Any) -> AsyncMock:
    """Build a minimal mock connection for the plain-INSERT write path."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=team_id)  # default_team_id lookup
    conn.fetch = AsyncMock(return_value=[])           # find_contradiction_candidates
    conn.fetchrow = AsyncMock(
        return_value={"id": memory_id, "version": 1, "created_at": datetime(2025, 1, 1, tzinfo=UTC)}
    )
    conn.execute = AsyncMock()
    return conn


# ---------------------------------------------------------------------------
# Tool-layer: nli_backend=none always returns contradictions=[]
# ---------------------------------------------------------------------------


class TestNliBackendNone:
    @pytest.mark.asyncio
    async def test_check_contradictions_returns_empty_list_when_nli_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """check_contradictions=True with nli_backend=none → contradictions: []."""
        _reset_settings_cache_for_tests()
        # Ensure nli_backend is "none" (the default; clear any stray env var)
        monkeypatch.delenv("MEM_MCP_NLI_BACKEND", raising=False)

        team_id = uuid4()
        memory_id = uuid4()
        conn = _make_conn_for_plain_insert(team_id, memory_id)
        _patch_tenant_tx(monkeypatch, conn)

        monkeypatch.setattr(
            "mem_mcp.mcp.tools.write.embed_or_skip",
            AsyncMock(return_value=([0.1] * 1024, 5, "ok")),
        )

        tool = MemoryWriteTool()
        ctx = _build_ctx()
        inp = MemoryWriteInput(content="we use PostgreSQL", type="fact", check_contradictions=True)
        result = await tool(ctx, inp)

        assert result.contradictions == []
        _reset_settings_cache_for_tests()

    @pytest.mark.asyncio
    async def test_check_contradictions_false_leaves_contradictions_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """check_contradictions=False (default) → contradictions: null."""
        _reset_settings_cache_for_tests()
        monkeypatch.delenv("MEM_MCP_NLI_BACKEND", raising=False)

        team_id = uuid4()
        memory_id = uuid4()
        conn = _make_conn_for_plain_insert(team_id, memory_id)
        _patch_tenant_tx(monkeypatch, conn)

        monkeypatch.setattr(
            "mem_mcp.mcp.tools.write.embed_or_skip",
            AsyncMock(return_value=([0.1] * 1024, 5, "ok")),
        )

        tool = MemoryWriteTool()
        ctx = _build_ctx()
        inp = MemoryWriteInput(content="we use PostgreSQL", type="fact")
        result = await tool(ctx, inp)

        assert result.contradictions is None
        _reset_settings_cache_for_tests()


# ---------------------------------------------------------------------------
# Tool-layer: Ollama timeout → write succeeds, contradictions=[]
# ---------------------------------------------------------------------------


class TestOllamaFailure:
    @pytest.mark.asyncio
    async def test_ollama_timeout_does_not_fail_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ollama connection error → write completes, contradictions: []."""
        import httpx

        _reset_settings_cache_for_tests()
        monkeypatch.setenv("MEM_MCP_NLI_BACKEND", "ollama")
        monkeypatch.setenv("MEM_MCP_OLLAMA_URL", "http://ollama-unreachable:11434")

        team_id = uuid4()
        memory_id = uuid4()
        conn = _make_conn_for_plain_insert(team_id, memory_id)
        # Return one row from the DB so the NLI path is exercised
        existing_id = uuid4()
        conn.fetch = AsyncMock(
            return_value=[
                {
                    "id": existing_id,
                    "content": "we use MySQL",
                    "type": "fact",
                    "tags": [],
                    "created_at": datetime(2025, 1, 1, tzinfo=UTC),
                }
            ]
        )
        _patch_tenant_tx(monkeypatch, conn)

        monkeypatch.setattr(
            "mem_mcp.mcp.tools.write.embed_or_skip",
            AsyncMock(return_value=([0.1] * 1024, 5, "ok")),
        )
        # Simulate Ollama being unreachable
        monkeypatch.setattr(
            "mem_mcp.memory.contradiction._score_contradiction",
            AsyncMock(side_effect=httpx.ConnectError("connection refused")),
        )

        tool = MemoryWriteTool()
        ctx = _build_ctx()
        inp = MemoryWriteInput(
            content="we use PostgreSQL", type="fact", check_contradictions=True
        )
        result = await tool(ctx, inp)

        assert result.id == memory_id
        assert result.contradictions == []
        _reset_settings_cache_for_tests()


# ---------------------------------------------------------------------------
# DB-layer: find_contradiction_candidates SQL correctness (requires live DB)
# ---------------------------------------------------------------------------


class TestFindCandidatesSQL:
    @pytest.mark.asyncio
    async def test_finds_recent_memory_of_same_type(self, pg_pool: Any) -> None:
        """find_contradiction_candidates fetches recent same-type memories."""
        tenant_id = uuid4()
        team_id = uuid4()

        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                    tenant_id,
                    f"contra-{tenant_id}@test.invalid",
                )
                await conn.execute(
                    "INSERT INTO teams (id, name, created_by_tenant_id) VALUES ($1, $2, $3)",
                    team_id,
                    f"contra-team-{team_id}",
                    tenant_id,
                )
                mem_id = await conn.fetchval(
                    """
                    INSERT INTO memories (
                        tenant_id, content, content_hash, type, tags,
                        metadata, version, is_current, indexable,
                        embedding_status, team_id, visibility
                    ) VALUES ($1, $2, md5($2), 'fact', ARRAY[]::text[],
                        '{}'::jsonb, 1, true, true, 'ok', $3, 'team')
                    RETURNING id
                    """,
                    tenant_id,
                    "we use MySQL",
                    team_id,
                )

                async with pg_pool.acquire() as query_conn:
                    with patch(
                        "mem_mcp.memory.contradiction._score_contradiction",
                        AsyncMock(return_value=0.9),
                    ):
                        results = await find_contradiction_candidates(
                            query_conn,
                            tenant_id,
                            "we use PostgreSQL",
                            "fact",
                            ollama_url="http://mock-ollama",
                            threshold=0.7,
                        )

                assert len(results) == 1
                assert results[0].memory_id == mem_id
                assert results[0].contradiction_score == pytest.approx(0.9)
                assert results[0].content_snippet == "we use MySQL"

    @pytest.mark.asyncio
    async def test_ignores_different_type(self, pg_pool: Any) -> None:
        """Memories of a different type are not returned."""
        tenant_id = uuid4()
        team_id = uuid4()

        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                    tenant_id,
                    f"contra2-{tenant_id}@test.invalid",
                )
                await conn.execute(
                    "INSERT INTO teams (id, name, created_by_tenant_id) VALUES ($1, $2, $3)",
                    team_id,
                    f"contra2-team-{team_id}",
                    tenant_id,
                )
                await conn.execute(
                    """
                    INSERT INTO memories (
                        tenant_id, content, content_hash, type, tags,
                        metadata, version, is_current, indexable,
                        embedding_status, team_id, visibility
                    ) VALUES ($1, $2, md5($2), 'note', ARRAY[]::text[],
                        '{}'::jsonb, 1, true, true, 'ok', $3, 'team')
                    """,
                    tenant_id,
                    "we use MySQL",
                    team_id,
                )

                async with pg_pool.acquire() as query_conn:
                    with patch(
                        "mem_mcp.memory.contradiction._score_contradiction",
                        AsyncMock(return_value=0.9),
                    ):
                        results = await find_contradiction_candidates(
                            query_conn,
                            tenant_id,
                            "we use PostgreSQL",
                            "fact",  # ← different type from the inserted "note"
                            ollama_url="http://mock-ollama",
                            threshold=0.7,
                        )

                assert results == []

    @pytest.mark.asyncio
    async def test_ignores_soft_deleted_memory(self, pg_pool: Any) -> None:
        """Soft-deleted memories are excluded from candidates."""
        tenant_id = uuid4()
        team_id = uuid4()

        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO tenants (id, email, status) VALUES ($1, $2, 'active')",
                    tenant_id,
                    f"contra3-{tenant_id}@test.invalid",
                )
                await conn.execute(
                    "INSERT INTO teams (id, name, created_by_tenant_id) VALUES ($1, $2, $3)",
                    team_id,
                    f"contra3-team-{team_id}",
                    tenant_id,
                )
                await conn.execute(
                    """
                    INSERT INTO memories (
                        tenant_id, content, content_hash, type, tags,
                        metadata, version, is_current, indexable,
                        embedding_status, team_id, visibility, deleted_at
                    ) VALUES ($1, $2, md5($2), 'fact', ARRAY[]::text[],
                        '{}'::jsonb, 1, true, true, 'ok', $3, 'team', now())
                    """,
                    tenant_id,
                    "we use MySQL (deleted)",
                    team_id,
                )

                async with pg_pool.acquire() as query_conn:
                    with patch(
                        "mem_mcp.memory.contradiction._score_contradiction",
                        AsyncMock(return_value=0.9),
                    ):
                        results = await find_contradiction_candidates(
                            query_conn,
                            tenant_id,
                            "we use PostgreSQL",
                            "fact",
                            ollama_url="http://mock-ollama",
                            threshold=0.7,
                        )

                assert results == []
