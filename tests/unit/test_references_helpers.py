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


async def _seed_natural_path(conn: asyncpg.Connection) -> dict[str, Any]:
    """Tenant + team + raw-SQL team_role_assignments insert (BYPASSING assign_role).

    Mimics migration 0026's personal-team backfill path: raw INSERT into
    team_role_assignments without calling assign_role/sync_refresh_on_user_assignment.
    Result: UEA row for (tenant, team) is NEVER created. The seeded tenant
    can READ memories via memories_select RLS's same-tenant clause, but
    pre-fix could NOT reference them (UEA-only validator gate).
    """
    tenant_id = await conn.fetchval(
        "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
        f"natural-{uuid4()}@example.test",
    )
    team_id = await conn.fetchval(
        "INSERT INTO teams (name, created_by_tenant_id, team_type) "
        "VALUES ($1, $2, 'personal') RETURNING id",
        f"natural-{uuid4().hex[:8]}",
        tenant_id,
    )
    # RAW INSERT — bypasses assign_role/sync_refresh path.
    owner_role_id = await conn.fetchval(
        "SELECT id FROM roles_catalog WHERE role_key = 'owner' AND plugin_id IS NULL"
    )
    await conn.execute(
        """
        INSERT INTO team_role_assignments
            (parent_team_id, member_kind, member_id, role_id, status, assigned_by_user_id)
        VALUES ($1, 'user', $2, $3, 'active', $2)
        """,
        team_id,
        tenant_id,
        owner_role_id,
    )
    # Verify UEA is EMPTY for (tenant, team) — guards the test premise.
    uea_row = await conn.fetchval(
        "SELECT 1 FROM user_effective_team_access "
        "WHERE user_id = $1 AND resource_team_id = $2",
        tenant_id,
        team_id,
    )
    assert uea_row is None, "natural-path fixture should NOT have populated UEA"
    mem = await conn.fetchval(
        "INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type, visibility) "
        "VALUES ($1, $2, 'natural', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision', 'team') RETURNING id",
        tenant_id,
        team_id,
        f"hash-{uuid4().hex}",
    )
    return {"tenant_id": tenant_id, "team_id": team_id, "memory_id": mem}


class TestSameTenantEscape:
    """Per bug fab23ec5 + DA ratification f74663a6: validator must allow
    same-tenant access without requiring a UEA row."""

    async def test_rv1_same_tenant_uea_present_uuid(self, pg_pool: Any) -> None:
        """RV-1: same-tenant + UEA present (assign_role path) → SUCCEEDS."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                s = await _seed(conn)
                got = await resolve_reference_target(
                    conn, target_uuid=s["memory_id"], caller_user_id=s["tenant_id"]
                )
                assert got["memory_id"] == s["memory_id"]

    async def test_rv3_same_tenant_uea_absent_uuid(self, pg_pool: Any) -> None:
        """RV-3: same-tenant, UEA absent (natural-path 0026 mimic) → SUCCEEDS post-fix.

        This is the bug fab23ec5 regression-trigger case: pre-fix this would
        opaque-reject because the validator's UEA-only gate fired and found
        nothing; post-fix the same-tenant escape clause fires before UEA
        is even consulted.
        """
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                s = await _seed_natural_path(conn)
                got = await resolve_reference_target(
                    conn, target_uuid=s["memory_id"], caller_user_id=s["tenant_id"]
                )
                assert got["memory_id"] == s["memory_id"]
                assert got["team_id"] == s["team_id"]

    async def test_rv7_same_tenant_uea_absent_slug(self, pg_pool: Any) -> None:
        """RV-7: same-tenant slug-tuple path, UEA absent → SUCCEEDS post-fix."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                s = await _seed_natural_path(conn)
                await insert_slug_with_retry(
                    conn,
                    team_id=s["team_id"],
                    resource_type="decision",
                    clue="natural-slug",
                    memory_id=s["memory_id"],
                    title="N",
                )
                got = await resolve_reference_target(
                    conn,
                    target_team_id=s["team_id"],
                    target_resource_type="decision",
                    target_slug="natural-slug",
                    caller_user_id=s["tenant_id"],
                )
                assert got["memory_id"] == s["memory_id"]

    async def test_rv9_revoked_then_same_tenant(self, pg_pool: Any) -> None:
        """RV-9: same-tenant target in a team caller was REVOKED from.

        Locks in that the same-tenant escape fires BEFORE UEA is consulted —
        revocation does NOT block own-tenant references.
        """
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                s = await _seed(conn)
                # Revoke the caller's membership (deletes UEA row too).
                await conn.execute(
                    "DELETE FROM user_effective_team_access "
                    "WHERE user_id = $1 AND resource_team_id = $2",
                    s["tenant_id"],
                    s["team_id"],
                )
                # Same-tenant escape should still let the reference through.
                got = await resolve_reference_target(
                    conn, target_uuid=s["memory_id"], caller_user_id=s["tenant_id"]
                )
                assert got["memory_id"] == s["memory_id"]

    async def test_rv5_cross_tenant_no_uea_still_rejects(self, pg_pool: Any) -> None:
        """RV-5 regression-lock: cross-tenant + UEA absent still opaque-rejects.

        IT-08 contract preserved. Caller is a different tenant with no
        UEA for the target's team.
        """
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                s = await _seed(conn)
                outsider = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"outsider-{uuid4()}@example.test",
                )
                with pytest.raises(ReferenceTargetNotFoundError) as exc:
                    await resolve_reference_target(
                        conn, target_uuid=s["memory_id"], caller_user_id=outsider
                    )
                assert "not found or not accessible" in str(exc.value)

    async def test_rv8_null_team_id_boundary(self, pg_pool: Any) -> None:
        """RV-8: target memory with team_id IS NULL (legacy pre-0026 shape).

        Helper short-circuits TRUE on p_resource_team IS NULL.
        Post-0026 unreachable via write path but tested for defence-in-depth.
        """
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                s = await _seed(conn)
                null_team_mem = await conn.fetchval(
                    "INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type, visibility) "
                    "VALUES ($1, NULL, 'null-team', $2, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision', 'team') RETURNING id",
                    s["tenant_id"],
                    f"hash-{uuid4().hex}",
                )
                got = await resolve_reference_target(
                    conn, target_uuid=null_team_mem, caller_user_id=s["tenant_id"]
                )
                assert got["memory_id"] == null_team_mem
                assert got["team_id"] is None


class TestHardDeleteSameTenantEscape:
    """RH-1..4: hard-delete citer filter honors same-tenant escape.

    Per DA ratification §A(4) + Reviewer §A4: same-tenant citers move
    from inaccessible_count → accessible_citers post-fix.
    """

    async def test_rh4_same_tenant_uea_absent_citer_surfaces(self, pg_pool: Any) -> None:
        """RH-4: same-tenant own-personal-team citer with UEA NEVER POPULATED.

        Pre-fix: counted as inaccessible. Post-fix: surfaces in accessible_citers.
        """
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                s = await _seed_natural_path(conn)
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
                # Post-fix: citer surfaces in accessible_citers, NOT inaccessible_count
                assert src in exc.value.accessible_citers
                assert exc.value.inaccessible_count == 0

    async def test_rh_cross_tenant_citer_still_counted_only(self, pg_pool: Any) -> None:
        """Cross-tenant citer caller has no UEA for → still counts to inaccessible_count.

        Regression-lock: IT-08 cross-team opacity preserved.
        """
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                s = await _seed(conn)
                # Cross-tenant citer in a different team
                other_tenant = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"other-{uuid4()}@example.test",
                )
                other_team = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ($1, $2) RETURNING id",
                    f"other-{uuid4().hex[:8]}",
                    other_tenant,
                )
                src = await conn.fetchval(
                    "INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type, visibility) "
                    "VALUES ($1, $2, 'src', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision', 'team') RETURNING id",
                    other_tenant,
                    other_team,
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
                assert src not in exc.value.accessible_citers
                assert exc.value.inaccessible_count == 1
