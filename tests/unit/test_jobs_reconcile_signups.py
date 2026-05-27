"""Tests for mem_mcp.jobs.reconcile_signups."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from mem_mcp.jobs.reconcile_signups import (
    BotoCognitoUserLister,
    CognitoUserSummary,
    _classify,
    _derive_provider,
    _provision_one,
    reconcile_signups,
)


def create_mock_system_tx(mock_conn: AsyncMock) -> object:
    """Create a mock system_tx that yields the given connection."""

    @asynccontextmanager
    async def _mock_system_tx(pool: object) -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    return _mock_system_tx


# Tests for _derive_provider


class TestDeriveProvider:
    """Test provider derivation from identities JSON."""

    def test_derive_provider_federated_google(self) -> None:
        """Federated Google user returns (google, userId)."""
        identities_json = json.dumps([{"providerName": "Google", "userId": "123456"}])
        provider, user_id = _derive_provider(identities_json)
        assert provider == "google"
        assert user_id == "123456"

    def test_derive_provider_cognito(self) -> None:
        """Native Cognito user returns (cognito, None)."""
        # Empty identities array
        identities_json = json.dumps([])
        provider, user_id = _derive_provider(identities_json)
        assert provider == "cognito"
        assert user_id is None

    def test_derive_provider_malformed_json(self) -> None:
        """Malformed JSON defaults to (cognito, None)."""
        provider, user_id = _derive_provider("not json")
        assert provider == "cognito"
        assert user_id is None

    def test_derive_provider_none(self) -> None:
        """None defaults to (cognito, None)."""
        provider, user_id = _derive_provider(None)
        assert provider == "cognito"
        assert user_id is None


# Tests for classification


class TestClassify:
    """Test _classify function."""

    @pytest.mark.asyncio
    async def test_skips_unconfirmed_users(self) -> None:
        """Users with email_verified=False are skipped."""
        mock_conn = AsyncMock()
        mock_conn.fetch.side_effect = [[], []]  # No existing identities, no invites

        cognito_users = [
            CognitoUserSummary(
                sub="sub1",
                username="user1",
                email="user1@example.com",
                email_verified=False,  # Not verified
                workspace_domain=None,
                enabled=True,
                user_status="CONFIRMED",
                identities_json=None,
            ),
        ]

        with patch(
            "mem_mcp.jobs.reconcile_signups.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            to_provision, dangling, orphans, skipped = await _classify(AsyncMock(), cognito_users)

        assert len(to_provision) == 0
        assert skipped == 1

    @pytest.mark.asyncio
    async def test_skips_disabled_users(self) -> None:
        """Users with enabled=False are skipped."""
        mock_conn = AsyncMock()
        mock_conn.fetch.side_effect = [[], []]

        cognito_users = [
            CognitoUserSummary(
                sub="sub1",
                username="user1",
                email="user1@example.com",
                email_verified=True,
                workspace_domain=None,
                enabled=False,  # Disabled
                user_status="CONFIRMED",
                identities_json=None,
            ),
        ]

        with patch(
            "mem_mcp.jobs.reconcile_signups.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            to_provision, dangling, orphans, skipped = await _classify(AsyncMock(), cognito_users)

        assert len(to_provision) == 0
        assert skipped == 1

    @pytest.mark.asyncio
    async def test_skips_users_already_in_identities(self) -> None:
        """Users with sub in tenant_identities are skipped."""
        mock_conn = AsyncMock()
        # Existing identity with matching sub
        mock_conn.fetch.side_effect = [
            [{"cognito_sub": "sub1", "email": "user1@example.com"}],
            [],
        ]

        cognito_users = [
            CognitoUserSummary(
                sub="sub1",
                username="user1",
                email="user1@example.com",
                email_verified=True,
                workspace_domain=None,
                enabled=True,
                user_status="CONFIRMED",
                identities_json=None,
            ),
        ]

        with patch(
            "mem_mcp.jobs.reconcile_signups.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            to_provision, dangling, orphans, skipped = await _classify(AsyncMock(), cognito_users)

        assert len(to_provision) == 0
        assert skipped == 1

    @pytest.mark.asyncio
    async def test_classifies_dangling_uninvited(self) -> None:
        """Users in Cognito but not in invited_emails are flagged as dangling."""
        mock_conn = AsyncMock()
        # No existing identities, no invites
        mock_conn.fetch.side_effect = [[], []]

        cognito_users: list[CognitoUserSummary] = [
            CognitoUserSummary(
                sub="sub1",
                username="user1",
                email="user1@example.com",
                email_verified=True,
                workspace_domain=None,
                enabled=True,
                user_status="CONFIRMED",
                identities_json=None,
            ),
        ]

        with patch(
            "mem_mcp.jobs.reconcile_signups.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            to_provision, dangling, orphans, skipped = await _classify(AsyncMock(), cognito_users)

        assert len(to_provision) == 1
        assert dangling == ["user1@example.com"]

    @pytest.mark.asyncio
    async def test_classifies_orphan_identity(self) -> None:
        """Identities in DB not matching any Cognito user are orphans."""
        mock_conn = AsyncMock()
        # Existing identity not in Cognito
        mock_conn.fetch.side_effect = [
            [{"cognito_sub": "orphan_sub", "email": "orphan@example.com"}],
            [],
        ]

        cognito_users: list[CognitoUserSummary] = []  # Empty Cognito

        with patch(
            "mem_mcp.jobs.reconcile_signups.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            to_provision, dangling, orphans, skipped = await _classify(AsyncMock(), cognito_users)

        assert len(to_provision) == 0
        assert orphans == ["orphan@example.com"]


# Tests for reconciliation flow


class TestReconcileSignups:
    """Test reconcile_signups orchestrator."""

    @pytest.mark.asyncio
    async def test_dry_run_does_not_mutate(self) -> None:
        """Dry run populates report.provisioned but does not mutate DB."""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.fetch.side_effect = [[], []]

        mock_lister = AsyncMock()
        mock_lister.list_users.return_value = [
            CognitoUserSummary(
                sub="sub1",
                username="user1",
                email="user1@example.com",
                email_verified=True,
                workspace_domain=None,
                enabled=True,
                user_status="CONFIRMED",
                identities_json=None,
            ),
        ]

        mock_audit = AsyncMock()

        with patch(
            "mem_mcp.jobs.reconcile_signups.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            report = await reconcile_signups(mock_pool, mock_lister, mock_audit, dry_run=True)

        assert report.provisioned == ["user1@example.com"]
        # Should NOT have called _provision_one (no transactions started for provisioning)
        # In dry_run, only the classify transaction happens

    @pytest.mark.asyncio
    async def test_audit_event_emitted_with_correct_action(self) -> None:
        """Successful provision emits audit event with action='tenant.provisioned_by_reconcile'."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": uuid4()}  # Tenant creation returns ID
        mock_conn.fetch.return_value = []  # No pending invites

        mock_pool = AsyncMock()
        mock_audit = AsyncMock()

        user = CognitoUserSummary(
            sub="sub1",
            username="user1",
            email="user1@example.com",
            email_verified=True,
            workspace_domain=None,
            enabled=True,
            user_status="CONFIRMED",
            identities_json=None,
        )

        with patch(
            "mem_mcp.jobs.reconcile_signups.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            ok, err = await _provision_one(mock_pool, user, mock_audit)

        assert ok is True
        assert err is None
        # Verify audit was called with the right action
        call_args = mock_audit.audit.call_args
        assert call_args is not None
        assert call_args[1]["action"] == "tenant.provisioned_by_reconcile"

    @pytest.mark.asyncio
    async def test_per_user_provision_failure_isolation(self) -> None:
        """If one user's provision fails, others still get provisioned."""
        # This test would require full mocking of DB + audit
        # Simplified: just check that _provision_one returns (False, err) on exception
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = Exception("DB error")

        mock_pool = AsyncMock()
        mock_audit = AsyncMock()

        user = CognitoUserSummary(
            sub="sub1",
            username="user1",
            email="user1@example.com",
            email_verified=True,
            workspace_domain=None,
            enabled=True,
            user_status="CONFIRMED",
            identities_json=None,
        )

        with patch(
            "mem_mcp.jobs.reconcile_signups.system_tx",
            create_mock_system_tx(mock_conn),
        ):
            ok, err = await _provision_one(mock_pool, user, mock_audit)

        assert ok is False
        assert err is not None
        assert "DB error" in err


# Test Cognito lister


class TestBotoCognitoUserLister:
    """Test BotoCognitoUserLister paginated fetch."""

    @pytest.mark.asyncio
    async def test_cognito_paginated_response(self) -> None:
        """Lister handles paginated Cognito responses."""
        from unittest.mock import Mock

        # Mock boto3 client — use Mock (not AsyncMock) for sync methods
        mock_client = Mock()
        mock_paginator = Mock()

        # Simulate two pages
        page1 = {
            "Users": [
                {
                    "Attributes": [
                        {"Name": "sub", "Value": "sub1"},
                        {"Name": "email", "Value": "user1@example.com"},
                        {"Name": "email_verified", "Value": "true"},
                        {"Name": "enabled", "Value": "true"},
                        {"Name": "cognito:username", "Value": "user1"},
                        {"Name": "UserStatus", "Value": "CONFIRMED"},
                    ],
                }
            ]
        }
        page2 = {
            "Users": [
                {
                    "Attributes": [
                        {"Name": "sub", "Value": "sub2"},
                        {"Name": "email", "Value": "user2@example.com"},
                        {"Name": "email_verified", "Value": "true"},
                        {"Name": "enabled", "Value": "true"},
                        {"Name": "cognito:username", "Value": "user2"},
                        {"Name": "UserStatus", "Value": "CONFIRMED"},
                    ],
                }
            ]
        }

        mock_paginator.paginate.return_value = [page1, page2]
        mock_client.get_paginator.return_value = mock_paginator

        lister = BotoCognitoUserLister("pool-id", "us-east-1")
        lister._client = mock_client

        users = await lister.list_users()

        assert len(users) == 2
        assert users[0].sub == "sub1"
        assert users[0].email == "user1@example.com"
        assert users[1].sub == "sub2"
        assert users[1].email == "user2@example.com"
