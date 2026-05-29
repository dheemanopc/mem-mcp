"""Integration test IT-06 — Spec 2 slug helpers at the conn level.

The end-to-end MemoryWriteTool→MemoryGetTool path was tried earlier but
fights the test pool (missing pgvector codec, oauth_clients schema mismatch).
The wired-tool e2e is left for a future PR that builds a proper pool factory
fixture; the Stage B get.py bug (inp.id vs lookup_id) is verified by
inspection + unit-level Pydantic tests.

This file covers the helper-layer contract: insert_slug_with_retry →
lookup_slug round-trip with a real memories row and a real slug row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from mem_mcp.teams.slugs import insert_slug_with_retry, lookup_slug

if TYPE_CHECKING:
    from asyncpg import Pool  # type: ignore[import-untyped]

pytestmark = pytest.mark.asyncio


async def test_slug_lookup_helper_roundtrip(pg_pool: Pool) -> None:
    """insert_slug_with_retry then lookup_slug returns the inserted row."""
    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            tenant_id = await conn.fetchval(
                "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                f"test-{uuid4()}@example.test",
            )
            team_id = await conn.fetchval(
                "INSERT INTO teams (name, created_by_tenant_id) VALUES ($1, $2) RETURNING id",
                f"it06-slug-test-{uuid4().hex[:8]}",
                tenant_id,
            )
            mem_id = await conn.fetchval(
                """
                INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding,
                                      source_kind, type, visibility)
                VALUES ($1, $2, 'test content', $3,
                        array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision', 'team')
                RETURNING id
                """,
                tenant_id,
                team_id,
                f"hash-{uuid4().hex}",
            )
            slug = await insert_slug_with_retry(
                conn,
                team_id=team_id,
                resource_type="decision",
                clue="direct-lookup-test",
                memory_id=mem_id,
                title="Test",
            )
            assert slug == "direct-lookup-test"

            slug_row = await lookup_slug(
                conn,
                team_id=team_id,
                resource_type="decision",
                slug="direct-lookup-test",
            )
            assert slug_row is not None
            assert slug_row["memory_id"] == mem_id
