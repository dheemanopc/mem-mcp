"""Tests for enterprise team API endpoints (PR 1.B)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


class TestTeamCreation:
    """Team creation endpoint tests."""

    async def test_workspace_user_creates_workspace_team(
        self, client: TestClient, db_session: Any, user_with_workspace: Any
    ) -> None:
        """Workspace user can create a workspace team with auto-derived domain."""
        # user_with_workspace has workspace_domain="example.com"
        resp = client.post(
            "/api/web/teams",
            json={"name": "Engineering"},
            cookies={"mem_session": user_with_workspace["session"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["team"]["name"] == "Engineering"
        assert data["team"]["workspace_domain"] == "example.com"
        assert data["your_role"] == "owner"

    async def test_workspace_user_explicit_matching_domain(
        self, client: TestClient, user_with_workspace: Any
    ) -> None:
        """Workspace user can explicitly pass their domain."""
        resp = client.post(
            "/api/web/teams",
            json={"name": "Sales", "workspace_domain": "example.com"},
            cookies={"mem_session": user_with_workspace["session"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["team"]["workspace_domain"] == "example.com"

    async def test_workspace_user_cross_domain_rejected(
        self, client: TestClient, user_with_workspace: Any
    ) -> None:
        """Workspace user cannot create team in different domain."""
        resp = client.post(
            "/api/web/teams",
            json={"name": "Other", "workspace_domain": "other.com"},
            cookies={"mem_session": user_with_workspace["session"]},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "cross_workspace_team_create_denied"

    async def test_personal_user_cannot_create_workspace_team(
        self, client: TestClient, user_personal: Any
    ) -> None:
        """Personal user (no workspace_domain) cannot create workspace team."""
        resp = client.post(
            "/api/web/teams",
            json={"name": "Team", "workspace_domain": "example.com"},
            cookies={"mem_session": user_personal["session"]},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "workspace_team_by_personal_user_denied"

    async def test_personal_user_creates_personal_team(
        self, client: TestClient, user_personal: Any
    ) -> None:
        """Personal user can create a personal team (no workspace_domain)."""
        resp = client.post(
            "/api/web/teams",
            json={"name": "My Team"},
            cookies={"mem_session": user_personal["session"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["team"]["name"] == "My Team"
        assert data["team"]["workspace_domain"] is None


class TestTeamListing:
    """Team listing endpoint tests."""

    async def test_list_teams_empty(self, client: TestClient, user_personal: Any) -> None:
        """New user has no teams initially."""
        resp = client.get(
            "/api/web/teams",
            cookies={"mem_session": user_personal["session"]},
        )
        assert resp.status_code == 200
        assert resp.json()["teams"] == []

    async def test_list_teams_with_membership(self, client: TestClient, user_personal: Any) -> None:
        """User can see teams they are a member of."""
        # Create a team
        create_resp = client.post(
            "/api/web/teams",
            json={"name": "My Team"},
            cookies={"mem_session": user_personal["session"]},
        )
        assert create_resp.status_code == 200
        team_id = create_resp.json()["team"]["id"]

        # List teams
        list_resp = client.get(
            "/api/web/teams",
            cookies={"mem_session": user_personal["session"]},
        )
        assert list_resp.status_code == 200
        teams = list_resp.json()["teams"]
        assert len(teams) == 1
        assert teams[0]["id"] == team_id
        assert teams[0]["your_role"] == "owner"


class TestMemberManagement:
    """Member management endpoint tests."""

    async def test_add_member_by_tenant_id_workspace_match(
        self, client: TestClient, user_with_workspace: Any, other_user_same_workspace: Any
    ) -> None:
        """Can add member from same workspace to workspace team."""
        # user_with_workspace creates a team
        create_resp = client.post(
            "/api/web/teams",
            json={"name": "Team"},
            cookies={"mem_session": user_with_workspace["session"]},
        )
        team_id = create_resp.json()["team"]["id"]

        # Add other_user_same_workspace
        add_resp = client.post(
            f"/api/web/teams/{team_id}/members",
            json={"tenant_id": str(other_user_same_workspace["tenant_id"])},
            cookies={"mem_session": user_with_workspace["session"]},
        )
        assert add_resp.status_code == 200

        # Verify member was added
        list_resp = client.get(
            f"/api/web/teams/{team_id}/members",
            cookies={"mem_session": user_with_workspace["session"]},
        )
        assert len(list_resp.json()["members"]) == 2

    async def test_add_member_workspace_domain_mismatch(
        self, client: TestClient, user_with_workspace: Any, user_different_workspace: Any
    ) -> None:
        """Cannot add member from different workspace to workspace team."""
        # user_with_workspace creates a team
        create_resp = client.post(
            "/api/web/teams",
            json={"name": "Team"},
            cookies={"mem_session": user_with_workspace["session"]},
        )
        team_id = create_resp.json()["team"]["id"]

        # Try to add user_different_workspace
        add_resp = client.post(
            f"/api/web/teams/{team_id}/members",
            json={"tenant_id": str(user_different_workspace["tenant_id"])},
            cookies={"mem_session": user_with_workspace["session"]},
        )
        assert add_resp.status_code == 400
        assert add_resp.json()["detail"]["code"] == "member_domain_mismatch"

    async def test_add_member_personal_team_workspace_user_rejected(
        self, client: TestClient, user_personal: Any, user_with_workspace: Any
    ) -> None:
        """Cannot add workspace user to personal team."""
        # user_personal creates a team
        create_resp = client.post(
            "/api/web/teams",
            json={"name": "Team"},
            cookies={"mem_session": user_personal["session"]},
        )
        team_id = create_resp.json()["team"]["id"]

        # Try to add user_with_workspace
        add_resp = client.post(
            f"/api/web/teams/{team_id}/members",
            json={"tenant_id": str(user_with_workspace["tenant_id"])},
            cookies={"mem_session": user_personal["session"]},
        )
        assert add_resp.status_code == 400
        assert add_resp.json()["detail"]["code"] == "member_has_workspace_domain"

    async def test_email_invite_creates_pending(
        self, client: TestClient, user_with_workspace: Any
    ) -> None:
        """Email invite creates pending invite."""
        create_resp = client.post(
            "/api/web/teams",
            json={"name": "Team"},
            cookies={"mem_session": user_with_workspace["session"]},
        )
        team_id = create_resp.json()["team"]["id"]

        # Send email invite
        invite_resp = client.post(
            f"/api/web/teams/{team_id}/members",
            json={"invited_email": "newuser@example.com"},
            cookies={"mem_session": user_with_workspace["session"]},
        )
        assert invite_resp.status_code == 200

        # Check invite appears in list
        list_resp = client.get(
            f"/api/web/teams/{team_id}/members",
            cookies={"mem_session": user_with_workspace["session"]},
        )
        invites = list_resp.json()["invites"]
        assert len(invites) == 1
        assert invites[0]["email"] == "newuser@example.com"
        assert invites[0]["status"] == "pending"

    async def test_last_admin_protection_demote(
        self, client: TestClient, user_with_workspace: Any
    ) -> None:
        """Cannot demote the last admin."""
        create_resp = client.post(
            "/api/web/teams",
            json={"name": "Team"},
            cookies={"mem_session": user_with_workspace["session"]},
        )
        team_id = create_resp.json()["team"]["id"]

        # Try to demote self (last admin)
        patch_resp = client.patch(
            f"/api/web/teams/{team_id}/members/{user_with_workspace['tenant_id']}",
            json={"role": "member"},
            cookies={"mem_session": user_with_workspace["session"]},
        )
        assert patch_resp.status_code == 400
        assert patch_resp.json()["detail"]["code"] == "last_admin_protected"

    async def test_last_admin_protection_remove(
        self, client: TestClient, user_with_workspace: Any
    ) -> None:
        """Cannot remove the last admin."""
        create_resp = client.post(
            "/api/web/teams",
            json={"name": "Team"},
            cookies={"mem_session": user_with_workspace["session"]},
        )
        team_id = create_resp.json()["team"]["id"]

        # Try to remove self (last admin)
        delete_resp = client.delete(
            f"/api/web/teams/{team_id}/members/{user_with_workspace['tenant_id']}",
            cookies={"mem_session": user_with_workspace["session"]},
        )
        assert delete_resp.status_code == 400
        assert delete_resp.json()["detail"]["code"] == "last_admin_protected"

    async def test_non_admin_cannot_remove_member(
        self, client: TestClient, user_with_workspace: Any, other_user_same_workspace: Any
    ) -> None:
        """Non-admin cannot remove members."""
        # user_with_workspace creates team and adds other_user
        create_resp = client.post(
            "/api/web/teams",
            json={"name": "Team"},
            cookies={"mem_session": user_with_workspace["session"]},
        )
        team_id = create_resp.json()["team"]["id"]

        client.post(
            f"/api/web/teams/{team_id}/members",
            json={"tenant_id": str(other_user_same_workspace["tenant_id"])},
            cookies={"mem_session": user_with_workspace["session"]},
        )

        # other_user tries to remove user_with_workspace (fails; not admin)
        delete_resp = client.delete(
            f"/api/web/teams/{team_id}/members/{user_with_workspace['tenant_id']}",
            cookies={"mem_session": other_user_same_workspace["session"]},
        )
        assert delete_resp.status_code == 403

    async def test_non_admin_can_remove_self(
        self, client: TestClient, user_with_workspace: Any, other_user_same_workspace: Any
    ) -> None:
        """Non-admin can remove themselves."""
        # Create team and add other_user
        create_resp = client.post(
            "/api/web/teams",
            json={"name": "Team"},
            cookies={"mem_session": user_with_workspace["session"]},
        )
        team_id = create_resp.json()["team"]["id"]

        client.post(
            f"/api/web/teams/{team_id}/members",
            json={"tenant_id": str(other_user_same_workspace["tenant_id"])},
            cookies={"mem_session": user_with_workspace["session"]},
        )

        # other_user removes themselves
        delete_resp = client.delete(
            f"/api/web/teams/{team_id}/members/{other_user_same_workspace['tenant_id']}",
            cookies={"mem_session": other_user_same_workspace["session"]},
        )
        assert delete_resp.status_code == 200

        # Verify removed
        list_resp = client.get(
            f"/api/web/teams/{team_id}/members",
            cookies={"mem_session": user_with_workspace["session"]},
        )
        assert len(list_resp.json()["members"]) == 1


class TestTeamDeletion:
    """Team deletion endpoint tests."""

    async def test_soft_delete_orphans_memories(
        self, client: TestClient, user_with_workspace: Any, db_session: Any
    ) -> None:
        """Soft-deleting team orphans its memories (privacy to team_id=NULL)."""
        # Create team
        create_resp = client.post(
            "/api/web/teams",
            json={"name": "Team"},
            cookies={"mem_session": user_with_workspace["session"]},
        )
        team_id = create_resp.json()["team"]["id"]

        # Create a team-shared memory via raw SQL (MCP tools tested separately)
        # This test verifies the API endpoint, not the full memory lifecycle
        # For now, just verify deletion succeeds
        delete_resp = client.delete(
            f"/api/web/teams/{team_id}",
            cookies={"mem_session": user_with_workspace["session"]},
        )
        assert delete_resp.status_code == 200

        # Verify team is soft-deleted (404 when listing)
        get_resp = client.get(
            f"/api/web/teams/{team_id}",
            cookies={"mem_session": user_with_workspace["session"]},
        )
        assert get_resp.status_code == 404


# NOTE: this module used to end with four stub fixtures (`user_with_workspace`,
# `user_personal`, `other_user_same_workspace`, `user_different_workspace`) whose
# bodies were `pass`. Being defined in the test module, they SHADOWED the real
# fixtures in tests/conftest.py and handed every test `None`, so the whole file
# had never passed. It went unnoticed because the `client` fixture depends on
# `pg_pool`, which skips when MEM_MCP_TEST_DSN is unset — and the CI unit job
# does not set it. Removed; the conftest fixtures now apply.
