"""Tests for admin dangling-users endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from mem_mcp.jobs.reconcile_signups import (
    CognitoUserSummary,
    ReconcileReport,
)
from mem_mcp.web.admin.dangling_users import (
    DanglingUsersResponse,
    ProvisionableUser,
    make_dangling_users_router,
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


class TestDanglingUsersEndpoints:
    """Real endpoint tests using FastAPI TestClient."""

    def test_get_returns_three_buckets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET /api/web/admin/dangling-users returns classified users."""
        from fastapi import Depends

        tenant_id = uuid4()

        # Mock the cognito lister
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

        mock_pool = AsyncMock()
        mock_audit = AsyncMock()

        # Override require_permission to return a simple async function
        async def mock_get_tenant_id() -> object:
            return tenant_id

        # Patch require_permission at module level to return our mock dependency
        monkeypatch.setattr(
            "mem_mcp.web.admin.dangling_users.require_permission",
            lambda perm, **kwargs: mock_get_tenant_id,
        )

        # Patch _DEP_MANAGE_TENANTS after import
        import mem_mcp.web.admin.dangling_users

        monkeypatch.setattr(
            mem_mcp.web.admin.dangling_users,
            "_DEP_MANAGE_TENANTS",
            Depends(mock_get_tenant_id),
        )

        # Create router
        router = make_dangling_users_router(
            pool=mock_pool, cognito_lister=mock_lister, audit=mock_audit
        )

        # Build app
        app = FastAPI()
        app.include_router(router)

        # Mock _classify to return known buckets
        classify_result = (
            [
                CognitoUserSummary(
                    sub="sub1",
                    username="user1",
                    email="user1@example.com",
                    email_verified=True,
                    workspace_domain=None,
                    enabled=True,
                    user_status="CONFIRMED",
                    identities_json=None,
                )
            ],
            ["dangling@example.com"],
            ["orphan@example.com"],
            1,
        )

        monkeypatch.setattr(
            "mem_mcp.web.admin.dangling_users._classify",
            AsyncMock(return_value=classify_result),
        )

        client = TestClient(app)
        response = client.get("/api/web/admin/dangling-users")

        assert response.status_code == 200
        data = response.json()
        assert "provisionable" in data
        assert "dangling_uninvited" in data
        assert "orphan_identities" in data
        assert "skipped_existing" in data
        assert data["dangling_uninvited"] == ["dangling@example.com"]
        assert data["orphan_identities"] == ["orphan@example.com"]
        assert data["skipped_existing"] == 1

    def test_get_requires_system_manage_tenants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET /api/web/admin/dangling-users requires SYSTEM_MANAGE_TENANTS permission."""
        from fastapi import Depends

        mock_lister = AsyncMock()
        mock_pool = AsyncMock()
        mock_audit = AsyncMock()

        # Mock the permission check to raise 403
        def mock_forbidden_check() -> object:
            raise HTTPException(status_code=403, detail="Forbidden")

        # Patch _DEP_MANAGE_TENANTS
        import mem_mcp.web.admin.dangling_users

        monkeypatch.setattr(
            mem_mcp.web.admin.dangling_users,
            "_DEP_MANAGE_TENANTS",
            Depends(mock_forbidden_check),
        )

        router = make_dangling_users_router(
            pool=mock_pool, cognito_lister=mock_lister, audit=mock_audit
        )

        app = FastAPI()
        app.include_router(router)

        client = TestClient(app)
        response = client.get("/api/web/admin/dangling-users")

        assert response.status_code == 403

    def test_post_calls_reconcile_signups_and_returns_report(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /api/web/admin/dangling-users/reconcile triggers job."""
        from fastapi import Depends

        tenant_id = uuid4()

        mock_lister = AsyncMock()
        mock_pool = AsyncMock()
        mock_audit = AsyncMock()

        # Mock the permission check
        async def mock_get_tenant_id() -> object:
            return tenant_id

        # Patch _DEP_MANAGE_TENANTS
        import mem_mcp.web.admin.dangling_users

        monkeypatch.setattr(
            mem_mcp.web.admin.dangling_users,
            "_DEP_MANAGE_TENANTS",
            Depends(mock_get_tenant_id),
        )

        router = make_dangling_users_router(
            pool=mock_pool, cognito_lister=mock_lister, audit=mock_audit
        )

        app = FastAPI()
        app.include_router(router)

        # Mock reconcile_signups
        mock_reconcile = AsyncMock(
            return_value=ReconcileReport(
                provisioned=["a@x.com"],
                skipped_existing=2,
                dangling_uninvited=[],
                orphan_identities=[],
            )
        )

        monkeypatch.setattr("mem_mcp.web.admin.dangling_users.reconcile_signups", mock_reconcile)

        client = TestClient(app)
        response = client.post("/api/web/admin/dangling-users/reconcile")

        assert response.status_code == 200
        data = response.json()
        assert data["provisioned"] == ["a@x.com"]
        assert data["provisioned_count"] == 1
        assert data["skipped_existing"] == 2
        assert data["dangling_uninvited"] == []
        assert data["orphan_identities"] == []

        # Verify reconcile_signups was called with dry_run=False
        assert mock_reconcile.await_count == 1
        call_kwargs = mock_reconcile.call_args[1]
        assert call_kwargs["dry_run"] is False

    def test_post_requires_system_manage_tenants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /api/web/admin/dangling-users/reconcile requires permission."""
        from fastapi import Depends

        mock_lister = AsyncMock()
        mock_pool = AsyncMock()
        mock_audit = AsyncMock()

        # Mock the permission check to raise 403
        def mock_forbidden_check() -> object:
            raise HTTPException(status_code=403, detail="Forbidden")

        # Patch _DEP_MANAGE_TENANTS
        import mem_mcp.web.admin.dangling_users

        monkeypatch.setattr(
            mem_mcp.web.admin.dangling_users,
            "_DEP_MANAGE_TENANTS",
            Depends(mock_forbidden_check),
        )

        router = make_dangling_users_router(
            pool=mock_pool, cognito_lister=mock_lister, audit=mock_audit
        )

        app = FastAPI()
        app.include_router(router)

        client = TestClient(app)
        response = client.post("/api/web/admin/dangling-users/reconcile")

        assert response.status_code == 403
