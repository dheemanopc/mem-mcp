"""Tests for tenant status enforcement (T-6.9, specs S-6 + S-7).

Verifies that suspended and pending-deletion tenants cannot make API calls.
These tests mirror the unit-level test_bearer_middleware.py tests but are
organized under the security/ suite.

Specs:
- S-6: Suspended tenant returns 403 with code 'account_suspended'
- S-7: Pending deletion tenant returns 403 with code 'account_deletion_pending'
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from mem_mcp.auth.jwt_validator import JwtClaims, JwtError
from mem_mcp.auth.middleware import (
    TenantContext,
    TenantResolution,
    make_bearer_middleware,
)

# --------------------------------------------------------------------------
# Fakes (reuse pattern from test_bearer_middleware.py)
# --------------------------------------------------------------------------


class FakeValidator:
    def __init__(self, claims: JwtClaims | None = None, error: JwtError | None = None) -> None:
        self._claims = claims
        self._error = error

    async def validate(self, token: str) -> JwtClaims:
        if self._error is not None:
            raise self._error
        assert self._claims is not None
        return self._claims


class FakeResolver:
    def __init__(self, resolution: TenantResolution) -> None:
        self.resolution = resolution

    async def resolve(self, cognito_sub: str, client_id: str) -> TenantResolution:
        return self.resolution


class FakeTouch:
    async def touch(self, identity_id: Any, client_id: str) -> None:
        pass


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _claims(
    sub: str = "cog-sub-1", client_id: str = "client-1", scopes: str = "memory.read memory.write"
) -> JwtClaims:
    return JwtClaims(
        sub=sub,
        iss="https://cognito-idp.ap-south-1.amazonaws.com/p1",
        client_id=client_id,
        token_use="access",
        scopes=tuple(scopes.split()),
        exp=2_000_000_000,
        iat=1_000_000_000,
    )


def _resolution(*, tenant_status: str = "active") -> TenantResolution:
    return TenantResolution(
        tenant_id=uuid4(),
        identity_id=uuid4(),
        tenant_status=tenant_status,  # type: ignore[arg-type]
        client_known=True,
        client_disabled=False,
    )


def _build_app(validator: Any, resolver: Any, touch: Any) -> TestClient:
    """Build a minimal FastAPI app with bearer middleware."""
    app = FastAPI()

    @app.post("/mcp")
    async def mcp_handler(request: Request) -> JSONResponse:
        ctx: TenantContext = request.state.tenant_ctx
        return JSONResponse(
            {
                "tenant_id": str(ctx.tenant_id),
                "identity_id": str(ctx.identity_id),
                "client_id": ctx.client_id,
            }
        )

    middleware = make_bearer_middleware(
        validator=validator,
        resolver=resolver,
        touch=touch,
        resource_metadata_url="https://memsys.dheemantech.in/.well-known/oauth-protected-resource",
        mcp_path_prefix="/mcp",
    )
    app.middleware("http")(middleware)
    return TestClient(app)


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class TestTenantStatusEnforcement:
    """Verify suspended and pending-deletion tenants are blocked."""

    @pytest.mark.security
    def test_suspended_tenant_returns_403_with_account_suspended(self) -> None:
        """Spec S-6: Suspended tenant returns 403 account_suspended."""
        validator = FakeValidator(claims=_claims())
        resolver = FakeResolver(_resolution(tenant_status="suspended"))
        client = _build_app(validator, resolver, FakeTouch())

        resp = client.post("/mcp", json={}, headers={"Authorization": "Bearer x"})
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"
        assert resp.json() == {
            "error": "account_suspended"
        }, f"Expected account_suspended error, got {resp.json()}"

    @pytest.mark.security
    def test_pending_deletion_tenant_returns_403_with_deletion_pending(self) -> None:
        """Spec S-7: Pending deletion tenant returns 403 account_deletion_pending."""
        validator = FakeValidator(claims=_claims())
        resolver = FakeResolver(_resolution(tenant_status="pending_deletion"))
        client = _build_app(validator, resolver, FakeTouch())

        resp = client.post("/mcp", json={}, headers={"Authorization": "Bearer x"})
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"
        assert resp.json() == {
            "error": "account_deletion_pending"
        }, f"Expected account_deletion_pending error, got {resp.json()}"

    @pytest.mark.security
    def test_active_tenant_succeeds(self) -> None:
        """Control case: active tenant passes through successfully."""
        validator = FakeValidator(claims=_claims())
        resolver = FakeResolver(_resolution(tenant_status="active"))
        client = _build_app(validator, resolver, FakeTouch())

        resp = client.post("/mcp", json={}, headers={"Authorization": "Bearer x"})
        assert (
            resp.status_code == 200
        ), f"Active tenant should succeed, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "tenant_id" in body and "identity_id" in body
