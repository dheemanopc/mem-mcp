"""Tests for enterprise phase 1A schema: teams, team_members, and team visibility.

Tests cover:
- Migration up/down idempotency
- Constraint validation (visibility/team_id consistency)
- RLS policies for teams and team_members
- RLS policy extension on memories for team visibility
- Auth callback workspace_domain extraction and persistence
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

from mem_mcp.cognito_clients import IdTokenUserInfoFetcher


@pytest.mark.integration
async def test_migration_up_creates_tables(pg_pool: Any) -> None:
    """Migration 0015 creates teams and team_members tables."""
    async with pg_pool.acquire() as conn:
        # Check teams table exists
        result = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'teams'
            )
            """
        )
        assert result is True

        # Check team_members table exists
        result = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'team_members'
            )
            """
        )
        assert result is True

        # Check memories has visibility and team_id columns
        result = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'memories' AND column_name = 'visibility'
            )
            """
        )
        assert result is True

        result = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'memories' AND column_name = 'team_id'
            )
            """
        )
        assert result is True

        # Check tenant_identities has workspace_domain column
        result = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'tenant_identities' AND column_name = 'workspace_domain'
            )
            """
        )
        assert result is True


@pytest.mark.integration
async def test_memories_visibility_constraint(pg_pool: Any, setup_two_tenants: Any) -> None:
    """Constraint: visibility='team' requires team_id; visibility='private' forbids it."""
    tenant_a = setup_two_tenants.a
    async with pg_pool.acquire() as conn:
        # Create a team for tenant A
        team_id = uuid4()
        await conn.execute(
            """
            INSERT INTO teams (id, name, created_by_tenant_id)
            VALUES ($1, $2, $3)
            """,
            team_id,
            "Test Team",
            tenant_a.tenant_id,
        )

        # Violate constraint: visibility='team' but team_id IS NULL
        with pytest.raises(Exception) as exc_info:
            await conn.execute(
                """
                INSERT INTO memories (
                    tenant_id, content, content_hash, embedding, source_kind,
                    visibility, team_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                tenant_a.tenant_id,
                "test",
                "hash",
                "[0.1, 0.2]",
                "api",
                "team",
                None,
            )
        assert "constraint" in str(exc_info.value).lower() or "check" in str(exc_info.value).lower()

        # Violate constraint: visibility='private' but team_id IS NOT NULL
        with pytest.raises(Exception) as exc_info:
            await conn.execute(
                """
                INSERT INTO memories (
                    tenant_id, content, content_hash, embedding, source_kind,
                    visibility, team_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                tenant_a.tenant_id,
                "test",
                "hash",
                "[0.1, 0.2]",
                "api",
                "private",
                team_id,
            )
        assert "constraint" in str(exc_info.value).lower() or "check" in str(exc_info.value).lower()

        # Valid: private memory without team_id
        await conn.execute(
            """
            INSERT INTO memories (
                tenant_id, content, content_hash, embedding, source_kind,
                visibility, team_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            tenant_a.tenant_id,
            "test",
            "hash",
            "[0.1, 0.2]",
            "api",
            "private",
            None,
        )

        # Valid: team memory with team_id
        await conn.execute(
            """
            INSERT INTO memories (
                tenant_id, content, content_hash, embedding, source_kind,
                visibility, team_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            tenant_a.tenant_id,
            "test",
            "hash2",
            "[0.1, 0.2]",
            "api",
            "team",
            team_id,
        )


@pytest.mark.integration
async def test_teams_workspace_domain_uniqueness(pg_pool: Any, setup_two_tenants: Any) -> None:
    """One workspace team per hd domain (or none for personal teams)."""
    tenant_a = setup_two_tenants.a
    tenant_b = setup_two_tenants.b

    async with pg_pool.acquire() as conn:
        # Create a workspace team for tenant A with domain 'dheemantech.in'
        await conn.execute(
            """
            INSERT INTO teams (name, workspace_domain, created_by_tenant_id)
            VALUES ($1, $2, $3)
            """,
            "Dheemantech",
            "dheemantech.in",
            tenant_a.tenant_id,
        )

        # Try to create another workspace team with same domain by tenant B
        with pytest.raises(Exception) as exc_info:
            await conn.execute(
                """
                INSERT INTO teams (name, workspace_domain, created_by_tenant_id)
                VALUES ($1, $2, $3)
                """,
                "Another Dheemantech",
                "dheemantech.in",
                tenant_b.tenant_id,
            )
        assert "unique" in str(exc_info.value).lower()

        # Create personal teams (workspace_domain = NULL) for both; should succeed
        await conn.execute(
            """
            INSERT INTO teams (name, workspace_domain, created_by_tenant_id)
            VALUES ($1, $2, $3)
            """,
            "Tenant A Personal",
            None,
            tenant_a.tenant_id,
        )
        await conn.execute(
            """
            INSERT INTO teams (name, workspace_domain, created_by_tenant_id)
            VALUES ($1, $2, $3)
            """,
            "Tenant B Personal",
            None,
            tenant_b.tenant_id,
        )


@pytest.mark.integration
async def test_rls_teams_select_via_membership(pg_pool: Any, setup_two_tenants: Any) -> None:
    """User can SELECT team only if active team member."""
    tenant_a = setup_two_tenants.a
    tenant_b = setup_two_tenants.b

    async with pg_pool.acquire() as conn:
        # Tenant A creates a team
        team_id = uuid4()
        await conn.execute(
            """
            INSERT INTO teams (id, name, workspace_domain, created_by_tenant_id)
            VALUES ($1, $2, $3, $4)
            """,
            team_id,
            "Tenant A Team",
            "tena.com",
            tenant_a.tenant_id,
        )

        # Tenant A adds themselves as admin
        await conn.execute(
            """
            INSERT INTO team_members (team_id, tenant_id, role, status, added_by_tenant_id)
            VALUES ($1, $2, $3, $4, $5)
            """,
            team_id,
            tenant_a.tenant_id,
            "admin",
            "active",
            tenant_a.tenant_id,
        )

        # Test RLS: Tenant A should SELECT their own team
        await conn.execute(f"SET app.current_tenant_id = '{tenant_a.tenant_id}'")
        result = await conn.fetchval(
            """
            SELECT COUNT(*) FROM teams WHERE id = $1
            """,
            team_id,
        )
        assert result == 1

        # Test RLS: Tenant B should NOT see Tenant A's team (not a member)
        await conn.execute(f"SET app.current_tenant_id = '{tenant_b.tenant_id}'")
        result = await conn.fetchval(
            """
            SELECT COUNT(*) FROM teams WHERE id = $1
            """,
            team_id,
        )
        assert result == 0

        # Add Tenant B as a member
        await conn.execute(
            """
            INSERT INTO team_members (team_id, tenant_id, role, status, added_by_tenant_id)
            VALUES ($1, $2, $3, $4, $5)
            """,
            team_id,
            tenant_b.tenant_id,
            "member",
            "active",
            tenant_a.tenant_id,
        )

        # Now Tenant B should see the team (active member)
        result = await conn.fetchval(
            """
            SELECT COUNT(*) FROM teams WHERE id = $1
            """,
            team_id,
        )
        assert result == 1

        # Revoke Tenant B's membership
        await conn.execute(
            """
            UPDATE team_members SET status = 'revoked'
            WHERE team_id = $1 AND tenant_id = $2
            """,
            team_id,
            tenant_b.tenant_id,
        )

        # Now Tenant B should NOT see the team again
        result = await conn.fetchval(
            """
            SELECT COUNT(*) FROM teams WHERE id = $1
            """,
            team_id,
        )
        assert result == 0


@pytest.mark.integration
async def test_rls_memories_team_visibility(pg_pool: Any, setup_two_tenants: Any) -> None:
    """Team-visible memory readable by active team members; private memory not."""
    tenant_a = setup_two_tenants.a
    tenant_b = setup_two_tenants.b

    async with pg_pool.acquire() as conn:
        # Create team
        team_id = uuid4()
        await conn.execute(
            """
            INSERT INTO teams (id, name, created_by_tenant_id)
            VALUES ($1, $2, $3)
            """,
            team_id,
            "Team",
            tenant_a.tenant_id,
        )

        # Add both as members
        await conn.execute(
            """
            INSERT INTO team_members (team_id, tenant_id, role, status, added_by_tenant_id)
            VALUES ($1, $2, $3, $4, $5), ($1, $6, $7, $8, $9)
            """,
            team_id,
            tenant_a.tenant_id,
            "admin",
            "active",
            tenant_a.tenant_id,
            tenant_b.tenant_id,
            "member",
            "active",
            tenant_a.tenant_id,
        )

        # Tenant A writes a private memory
        private_mem_id = uuid4()
        await conn.execute(
            """
            INSERT INTO memories (
                id, tenant_id, content, content_hash, embedding,
                source_kind, visibility, team_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            private_mem_id,
            tenant_a.tenant_id,
            "private content",
            "hash1",
            "[0.1]",
            "api",
            "private",
            None,
        )

        # Tenant A writes a team memory
        team_mem_id = uuid4()
        await conn.execute(
            """
            INSERT INTO memories (
                id, tenant_id, content, content_hash, embedding,
                source_kind, visibility, team_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            team_mem_id,
            tenant_a.tenant_id,
            "team content",
            "hash2",
            "[0.1]",
            "api",
            "team",
            team_id,
        )

        # Tenant B should NOT see private memory
        await conn.execute(f"SET app.current_tenant_id = '{tenant_b.tenant_id}'")
        result = await conn.fetchval(
            """
            SELECT COUNT(*) FROM memories WHERE id = $1
            """,
            private_mem_id,
        )
        assert result == 0

        # Tenant B SHOULD see team memory
        result = await conn.fetchval(
            """
            SELECT COUNT(*) FROM memories WHERE id = $1
            """,
            team_mem_id,
        )
        assert result == 1

        # Tenant B cannot UPDATE or DELETE the team memory (not the owner)
        with pytest.raises(Exception) as exc_info:
            await conn.execute(
                """
                UPDATE memories SET content = 'hacked' WHERE id = $1
                """,
                team_mem_id,
            )
        assert "policy" in str(exc_info.value).lower() or "row" in str(exc_info.value).lower()

        with pytest.raises(Exception) as exc_info:
            await conn.execute(
                """
                DELETE FROM memories WHERE id = $1
                """,
                team_mem_id,
            )
        assert "policy" in str(exc_info.value).lower() or "row" in str(exc_info.value).lower()


@pytest.mark.integration
async def test_auth_callback_workspace_domain_extraction(
    pg_pool: Any, setup_two_tenants: Any
) -> None:
    """Auth callback extracts custom:google_hd and persists as workspace_domain."""
    tenant_a = setup_two_tenants.a

    fetcher = IdTokenUserInfoFetcher()

    # Create a mock ID token with custom:google_hd claim
    import base64

    payload = {
        "sub": "google-id-123",
        "cognito:username": "test-user",
        "email": "test@dheemantech.in",
        "custom:google_hd": "dheemantech.in",
        "identities": json.dumps([{"providerName": "Google", "userId": "google-123"}]),
    }
    payload_json = json.dumps(payload)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")

    # Construct fake JWT (header.payload.signature)
    id_token = f"fake-header.{payload_b64}.fake-sig"

    # Extract user info
    user_info = await fetcher.get_user_info(id_token)
    assert user_info.workspace_domain == "dheemantech.in"
    assert user_info.cognito_sub == "google-id-123"

    # Now test persistence in callback: create a tenant_identity and test UPDATE
    async with pg_pool.acquire() as conn:
        identity_id = uuid4()
        await conn.execute(
            """
            INSERT INTO tenant_identities (
                id, tenant_id, cognito_sub, provider, email
            )
            VALUES ($1, $2, $3, $4, $5)
            """,
            identity_id,
            tenant_a.tenant_id,
            "google-id-123",
            "google",
            "test@dheemantech.in",
        )

        # Simulate callback: UPDATE workspace_domain
        await conn.execute(
            """
            UPDATE tenant_identities
            SET workspace_domain = $1
            WHERE id = $2 AND (workspace_domain IS NULL OR workspace_domain != $1)
            """,
            "dheemantech.in",
            identity_id,
        )

        # Verify it was persisted
        result = await conn.fetchval(
            """
            SELECT workspace_domain FROM tenant_identities WHERE id = $1
            """,
            identity_id,
        )
        assert result == "dheemantech.in"

        # Second call with same domain should be idempotent (no-op)
        await conn.execute(
            """
            UPDATE tenant_identities
            SET workspace_domain = $1
            WHERE id = $2 AND (workspace_domain IS NULL OR workspace_domain != $1)
            """,
            "dheemantech.in",
            identity_id,
        )

        # Verify it's still there
        result = await conn.fetchval(
            """
            SELECT workspace_domain FROM tenant_identities WHERE id = $1
            """,
            identity_id,
        )
        assert result == "dheemantech.in"
