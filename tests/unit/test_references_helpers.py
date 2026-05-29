"""Tests for teams.references — validator + graph + filtered hard-delete."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from mem_mcp.teams.assignments import assign_role
from mem_mcp.teams.references import (
    HardDeleteBlockedError,
    ReferenceTargetNotFoundError,
    check_hard_delete_with_filtered_citers,
    get_inbound_refs,
    get_outbound_refs,
    insert_reference,
    resolve_reference_target,
)
from mem_mcp.teams.slugs import insert_slug_with_retry

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


pytestmark = pytest.mark.asyncio


async def _seed(conn: asyncpg.Connection) -> dict[str, Any]:
    """Tenant + team + caller has 'admin' membership + memory."""
    tenant_id = await conn.fetchval(
        "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
        f"test-{uuid4()}@example.test",
    )
    team_id = await conn.fetchval(
        "INSERT INTO teams (name, created_by_tenant_id) VALUES ($1, $2) RETURNING id",
        f"ref-{uuid4().hex[:8]}",
        tenant_id,
    )
    await assign_role(
        conn,
        parent_team_id=team_id,
        member_kind="user",
        member_id=tenant_id,
        role_key="admin",
        assigned_by_user_id=tenant_id,
    )
    mem = await conn.fetchval(
        "INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type, visibility) "
        "VALUES ($1, $2, 'c', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision', 'team') RETURNING id",
        tenant_id,
        team_id,
        f"hash-{uuid4().hex}",
    )
    return {"tenant_id": tenant_id, "team_id": team_id, "memory_id": mem}


class TestResolveByUuid:
    async def test_hit(self, pg_pool: Any) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                s = await _seed(conn)
                got = await resolve_reference_target(
                    conn,
                    target_uuid=s["memory_id"],
                    caller_user_id=s["tenant_id"],
                )
                assert got["memory_id"] == s["memory_id"]
                assert got["team_id"] == s["team_id"]

    async def test_uuid_not_found_opaque_error(self, pg_pool: Any) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                s = await _seed(conn)
                with pytest.raises(ReferenceTargetNotFoundError) as exc:
                    await resolve_reference_target(
                        conn,
                        target_uuid=uuid4(),
                        caller_user_id=s["tenant_id"],
                    )
                # Same message used for both not-found AND no-access (opaque per amendment IT-08)
                assert "not found or not accessible" in str(exc.value)

    async def test_uuid_no_access_opaque_error(self, pg_pool: Any) -> None:
        """User has no access to the team that owns the memory → same opaque error."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                s = await _seed(conn)
                # Different user, no membership in s["team_id"]
                outsider = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"out-{uuid4()}@example.test",
                )
                with pytest.raises(ReferenceTargetNotFoundError) as exc:
                    await resolve_reference_target(
                        conn,
                        target_uuid=s["memory_id"],
                        caller_user_id=outsider,
                    )
                assert "not found or not accessible" in str(exc.value)


class TestResolveBySlug:
    async def test_hit(self, pg_pool: Any) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                s = await _seed(conn)
                await insert_slug_with_retry(
                    conn,
                    team_id=s["team_id"],
                    resource_type="decision",
                    clue="naming-conv",
                    memory_id=s["memory_id"],
                    title="T",
                )
                got = await resolve_reference_target(
                    conn,
                    target_team_id=s["team_id"],
                    target_resource_type="decision",
                    target_slug="naming-conv",
                    caller_user_id=s["tenant_id"],
                )
                assert got["memory_id"] == s["memory_id"]

    async def test_slug_not_found_opaque(self, pg_pool: Any) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                s = await _seed(conn)
                with pytest.raises(ReferenceTargetNotFoundError):
                    await resolve_reference_target(
                        conn,
                        target_team_id=s["team_id"],
                        target_resource_type="decision",
                        target_slug="does-not-exist",
                        caller_user_id=s["tenant_id"],
                    )


class TestGraphQueries:
    async def test_inbound_outbound(self, pg_pool: Any) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                s = await _seed(conn)
                # Second memory in same team for src
                src = await conn.fetchval(
                    "INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type, visibility) "
                    "VALUES ($1, $2, 'src', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision', 'team') RETURNING id",
                    s["tenant_id"],
                    s["team_id"],
                    f"hash-{uuid4().hex}",
                )
                await insert_reference(
                    conn,
                    source_memory_id=src,
                    target_memory_id=s["memory_id"],
                    target_team_id=s["team_id"],
                    reference_kind="cites",
                )
                inbound = await get_inbound_refs(conn, target_memory_id=s["memory_id"])
                outbound = await get_outbound_refs(conn, source_memory_id=src)
                assert len(inbound) == 1
                assert len(outbound) == 1


class TestHardDeleteFilteredCiters:
    async def test_zero_inbound_returns_silently(self, pg_pool: Any) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                s = await _seed(conn)
                await check_hard_delete_with_filtered_citers(
                    conn,
                    target_memory_id=s["memory_id"],
                    caller_user_id=s["tenant_id"],
                )  # no raise

    async def test_accessible_citer_uuid_in_list(self, pg_pool: Any) -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                s = await _seed(conn)
                src = await conn.fetchval(
                    "INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type, visibility) "
                    "VALUES ($1, $2, 'src', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision', 'team') RETURNING id",
                    s["tenant_id"],
                    s["team_id"],
                    f"hash-{uuid4().hex}",
                )
                await insert_reference(
                    conn,
                    source_memory_id=src,
                    target_memory_id=s["memory_id"],
                    target_team_id=s["team_id"],
                    reference_kind="cites",
                )
                with pytest.raises(HardDeleteBlockedError) as exc:
                    await check_hard_delete_with_filtered_citers(
                        conn,
                        target_memory_id=s["memory_id"],
                        caller_user_id=s["tenant_id"],
                    )
                assert src in exc.value.accessible_citers
                assert exc.value.inaccessible_count == 0
