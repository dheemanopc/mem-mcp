"""Integration tests for contradiction detection (issue #315) — real PG.

Covers the DB-touching half of find_contradiction_candidates: the recency-
window candidate fetch (type scoping, deleted/superseded exclusion, window
cap). The Ollama NLI call is patched — the wired-tool e2e against a real
Ollama is out of scope (same pool-factory limitation noted in
test_spec2_e2e_write_get_slug.py).

Skips cleanly when MEM_MCP_TEST_DSN is unset (pg_pool fixture).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from mem_mcp.memory.contradiction import find_contradiction_candidates

if TYPE_CHECKING:
    from asyncpg import Pool  # type: ignore[import-untyped]

pytestmark = pytest.mark.asyncio


def _patch_classify_all_contradict(
    monkeypatch: pytest.MonkeyPatch, score: float = 0.9
) -> list[str]:
    """Every candidate classifies as CONTRADICTION; returns the call log."""
    calls: list[str] = []

    async def fake_classify(
        *, url: str, model: str, new_content: str, candidate_content: str, timeout_seconds: float
    ) -> tuple[str, float]:
        calls.append(candidate_content)
        return ("CONTRADICTION", score)

    monkeypatch.setattr(
        "mem_mcp.memory.contradiction._ollama_nli_classify",
        fake_classify,
    )
    return calls


async def _seed_tenant_and_team(conn: Any) -> tuple[Any, Any]:
    tenant_id = await conn.fetchval(
        "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
        f"contradiction-{uuid4()}@example.test",
    )
    team_id = await conn.fetchval(
        "INSERT INTO teams (name, created_by_tenant_id) VALUES ($1, $2) RETURNING id",
        f"contradiction-test-{uuid4().hex[:8]}",
        tenant_id,
    )
    return tenant_id, team_id


async def _insert_memory(
    conn: Any,
    tenant_id: Any,
    team_id: Any,
    content: str,
    *,
    type_: str = "note",
    is_current: bool = True,
    deleted: bool = False,
) -> Any:
    return await conn.fetchval(
        """
        INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding,
                              source_kind, type, visibility, is_current, deleted_at)
        VALUES ($1, $2, $3, $4, array_fill(0::real, ARRAY[1024])::vector,
                'api', $5, 'team', $6, CASE WHEN $7 THEN now() ELSE NULL END)
        RETURNING id
        """,
        tenant_id,
        team_id,
        content,
        f"hash-{uuid4().hex}",
        type_,
        is_current,
        deleted,
    )


async def test_candidate_fetch_scoping_and_exclusions(
    pg_pool: Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only live, current, same-type, same-tenant rows become candidates."""
    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            tenant_id, team_id = await _seed_tenant_and_team(conn)
            other_tenant, other_team = await _seed_tenant_and_team(conn)

            live_id = await _insert_memory(conn, tenant_id, team_id, "we use MySQL")
            await _insert_memory(conn, tenant_id, team_id, "deleted row", deleted=True)
            await _insert_memory(conn, tenant_id, team_id, "superseded row", is_current=False)
            await _insert_memory(conn, tenant_id, team_id, "other type", type_="snippet")
            await _insert_memory(conn, other_tenant, other_team, "other tenant row")

            calls = _patch_classify_all_contradict(monkeypatch)
            out = await find_contradiction_candidates(
                conn,
                tenant_id,
                "we use PostgreSQL",
                "note",
                ollama_url="http://unused",
            )

            assert calls == ["we use MySQL"]
            assert [c.memory_id for c in out] == [live_id]
            assert out[0].contradiction_score == 0.9
            assert out[0].type == "note"


async def test_window_caps_candidates_to_most_recent(
    pg_pool: Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With window=2, only the 2 most recent memories are NLI-screened."""
    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            tenant_id, team_id = await _seed_tenant_and_team(conn)
            for i in range(4):
                await _insert_memory(conn, tenant_id, team_id, f"memory {i}")
                # created_at has now() default within one tx; force ordering
                await conn.execute(
                    "UPDATE memories SET created_at = now() - ($1 || ' minutes')::interval "
                    "WHERE tenant_id = $2 AND content = $3",
                    str(10 - i),
                    tenant_id,
                    f"memory {i}",
                )

            calls = _patch_classify_all_contradict(monkeypatch)
            out = await find_contradiction_candidates(
                conn,
                tenant_id,
                "new content",
                "note",
                ollama_url="http://unused",
                window=2,
                limit=10,
            )

            assert sorted(calls) == ["memory 2", "memory 3"]  # the 2 most recent
            assert len(out) == 2
