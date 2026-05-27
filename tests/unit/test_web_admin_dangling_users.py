"""Tests for admin dangling-users endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock

from mem_mcp.jobs.reconcile_signups import (
    ReconcileReport,
)
from mem_mcp.web.admin.dangling_users import (
    DanglingUsersResponse,
    ProvisionableUser,
)


class TestDanglingUsersResponseModel:
    """Test DanglingUsersResponse model."""

    def test_response_model_structure(self) -> None:
        """DanglingUsersResponse has expected fields."""
        response = DanglingUsersResponse(
            provisionable=[
                ProvisionableUser(
                    email="user1@example.com",
                    sub="sub1",
                    workspace_domain=None,
                ),
            ],
            dangling_uninvited=["dangling@example.com"],
            orphan_identities=["orphan@example.com"],
            skipped_existing=1,
        )

        assert len(list(response.provisionable)) == 1
        assert response.provisionable[0].email == "user1@example.com"
        assert response.dangling_uninvited == ["dangling@example.com"]
        assert response.orphan_identities == ["orphan@example.com"]
        assert response.skipped_existing == 1

    def test_router_factory_accepts_params(self) -> None:
        """make_dangling_users_router accepts pool, cognito_lister, audit."""
        from mem_mcp.web.admin.dangling_users import make_dangling_users_router

        mock_pool = AsyncMock()
        mock_lister = AsyncMock()
        mock_audit = AsyncMock()

        router = make_dangling_users_router(
            pool=mock_pool,
            cognito_lister=mock_lister,
            audit=mock_audit,
        )

        # Router is returned without error
        assert router is not None

    def test_reconcile_report_serialization(self) -> None:
        """ReconcileReport can be converted to dict for JSON response."""
        report = ReconcileReport(
            provisioned=["user1@example.com"],
            skipped_existing=2,
            dangling_uninvited=["dangling@example.com"],
            orphan_identities=["orphan@example.com"],
        )

        # Simulate what the endpoint would return
        response_dict = {
            "provisioned": report.provisioned,
            "provisioned_count": len(report.provisioned),
            "skipped_existing": report.skipped_existing,
            "dangling_uninvited": report.dangling_uninvited,
            "orphan_identities": report.orphan_identities,
        }

        assert response_dict["provisioned_count"] == 1
        assert response_dict["skipped_existing"] == 2
        dangling = response_dict["dangling_uninvited"]
        assert isinstance(dangling, list) and len(dangling) == 1
