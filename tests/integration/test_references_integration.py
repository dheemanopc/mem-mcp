"""Integration tests for memory_references — IT-08 + IT-09 + multi-team filter behavior."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from mem_mcp.teams.assignments import assign_role
from mem_mcp.teams.references import (
    HardDeleteBlockedError,
    ReferenceTargetNotFoundError,
    check_hard_delete_with_filtered_citers,
    insert_reference,
    resolve_reference_target,
)

pytestmark = pytest.mark.asyncio


class TestIt08OpaqueResolutionFailures:
    async def test_uuid_miss_and_no_access_produce_identical_error(self, pg_pool: Any) -> None:
        """Per amendment IT-08: 'not found' and 'no access' MUST be indistinguishable."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                tenant_a = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                tenant_b = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"test-{uuid4()}@example.test",
                )
                team_a = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it08-a', $1) RETURNING id",
                    tenant_a,
                )
                await assign_role(
                    conn,
                    parent_team_id=team_a,
                    member_kind="user",
                    member_id=tenant_a,
                    role_key="member",
                    assigned_by_user_id=tenant_a,
                )
                mem = await conn.fetchval(
                    "INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type, visibility) "
                    "VALUES ($1, $2, 'c', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision', 'team') RETURNING id",
                    tenant_a,
                    team_a,
                    f"hash-{uuid4().hex}",
                )

                # tenant_b has NO access to team_a's mem
                err_no_access = None
                try:
                    await resolve_reference_target(
                        conn,
                        target_uuid=mem,
                        caller_user_id=tenant_b,
                        tenant_id=tenant_a,
                    )
                except ReferenceTargetNotFoundError as e:
                    err_no_access = str(e)

                # Same caller, a UUID that simply doesn't exist
                err_miss = None
                try:
                    await resolve_reference_target(
                        conn,
                        target_uuid=uuid4(),
                        caller_user_id=tenant_b,
                        tenant_id=tenant_a,
                    )
                except ReferenceTargetNotFoundError as e:
                    err_miss = str(e)

                assert err_no_access is not None
                assert err_miss is not None
                assert err_no_access == err_miss  # opaque per amendment IT-08


class TestIt09FilteredCitersOnHardDelete:
    async def test_mixed_accessible_and_inaccessible_citers(self, pg_pool: Any) -> None:
        """3 citers: 2 in caller-accessible teams, 1 in inaccessible team.
        check_hard_delete_with_filtered_citers raises with 2 UUIDs + inaccessible_count=1."""
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                # Tenant + 2 teams caller can read + 1 they cannot
                caller = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"caller-{uuid4()}@example.test",
                )
                stranger = await conn.fetchval(
                    "INSERT INTO tenants (email) VALUES ($1) RETURNING id",
                    f"stranger-{uuid4()}@example.test",
                )
                t_target = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it09-tgt', $1) RETURNING id",
                    caller,
                )
                t_acc1 = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it09-a1', $1) RETURNING id",
                    caller,
                )
                t_acc2 = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it09-a2', $1) RETURNING id",
                    caller,
                )
                t_inacc = await conn.fetchval(
                    "INSERT INTO teams (name, created_by_tenant_id) VALUES ('it09-x', $1) RETURNING id",
                    stranger,
                )

                # Caller has access to t_target, t_acc1, t_acc2 — NOT t_inacc.
                for t in (t_target, t_acc1, t_acc2):
                    await assign_role(
                        conn,
                        parent_team_id=t,
                        member_kind="user",
                        member_id=caller,
                        role_key="member",
                        assigned_by_user_id=caller,
                    )
                # Stranger owns t_inacc
                await assign_role(
                    conn,
                    parent_team_id=t_inacc,
                    member_kind="user",
                    member_id=stranger,
                    role_key="owner",
                    assigned_by_user_id=stranger,
                )

                async def _mk(team_id: UUID, owner: UUID) -> UUID:
                    mem_id = await conn.fetchval(
                        "INSERT INTO memories (tenant_id, team_id, content, content_hash, embedding, source_kind, type, visibility) "
                        "VALUES ($1, $2, 'c', $3, array_fill(0::real, ARRAY[1024])::vector, 'api', 'decision', 'team') RETURNING id",
                        owner,
                        team_id,
                        f"hash-{uuid4().hex}",
                    )
                    assert isinstance(mem_id, UUID)
                    return mem_id

                target_mem: UUID = await _mk(t_target, caller)
                src1: UUID = await _mk(t_acc1, caller)
                src2: UUID = await _mk(t_acc2, caller)
                src3: UUID = await _mk(t_inacc, stranger)

                for src, _team in [(src1, t_acc1), (src2, t_acc2), (src3, t_inacc)]:
                    await insert_reference(
                        conn,
                        source_memory_id=src,
                        target_memory_id=target_mem,
                        target_team_id=t_target,
                        reference_kind="cites",
                    )

                with pytest.raises(HardDeleteBlockedError) as exc:
                    await check_hard_delete_with_filtered_citers(
                        conn,
                        target_memory_id=target_mem,
                        caller_user_id=caller,
                        tenant_id=caller,
                    )

                # Caller sees the 2 accessible-team UUIDs + count of 1 inaccessible
                accessible_set = set(exc.value.accessible_citers)
                assert accessible_set == {src1, src2}
                assert exc.value.inaccessible_count == 1
                # CRITICAL: src3's UUID is NOT in the accessible list
                assert src3 not in accessible_set
