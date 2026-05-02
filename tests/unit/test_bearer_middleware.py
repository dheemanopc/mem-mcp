"""Tests for mem_mcp.auth.middleware (T-4.3)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

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


_RESOURCE_METADATA = "https://memsys.dheemantech.in/.well-known/oauth-protected-resource"


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeValidator:
    def __init__(self, claims: JwtClaims | None = None, error: JwtError | None = None) -> None:
        self._claims = claims
        self._error = error
        self.calls: list[str] = []

    async def validate(self, token: str) -> JwtClaims:
        self.calls.append(token)
        if self._error is not None:
            raise self._error
        assert self._claims is not None
        return self._claims


class FakeResolver:
    def __init__(self, resolution: TenantResolution) -> None:
        self.resolution = resolution
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, cognito_sub: str, client_id: str) -> TenantResolution:
        self.calls.append((cognito_sub, client_id))
        return self.resolution


class FakeTouch:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str]] = []

    async def touch(self, identity_id: UUID, client_id: str) -> None:
        self.calls.append((identity_id, client_id))


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _claims(sub: str = "cog-sub-1", client_id: str = "client-1", scopes: str = "memory.read memory.write") -> JwtClaims:
    return JwtClaims(
        sub=sub,
        iss="https://cognito-idp.ap-south-1.amazonaws.com/p1",
        client_id=client_id,
        token_use="access",
        scopes=tuple(scopes.split()),
        exp=2_000_000_000,
        iat=1_000_000_000,
    )


def _resolution(
    *,
    tenant_status: str = "active",
    client_known: bool = True,
    client_disabled: bool = False,
    tenant_id: UUID | None = None,
    identity_id: UUID | None = None,
) -> TenantResolution:
    if tenant_status == "not_found":
        return TenantResolution(None, None, "not_found", False, False)
    return TenantResolution(
        tenant_id=tenant_id or uuid4(),
        identity_id=identity_id or uuid4(),
        tenant_status=tenant_status,  # type: ignore[arg-type]
        client_known=client_known,
        client_disabled=client_disabled,
    )


def _build_app(
    validator: Any, resolver: Any, touch: Any, mcp_prefix: str = "/mcp"
) -> TestClient:
    app = FastAPI()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(mcp_prefix)
    async def mcp_handler(request: Request) -> JSONResponse:
        ctx: TenantContext = request.state.tenant_ctx
        return JSONResponse(
            {
                "tenant_id": str(ctx.tenant_id),
                "identity_id": str(ctx.identity_id),
                "client_id": ctx.client_id,
                "scopes": sorted(ctx.scopes),
            }
        )

    middleware = make_bearer_middleware(
        validator=validator,
        resolver=resolver,
        touch=touch,
        resource_metadata_url=_RESOURCE_METADATA,
        mcp_path_prefix=mcp_prefix,
    )
    app.middleware("http")(middleware)
    return TestClient(app)


# --------------------------------------------------------------------------
# Path skipping
# --------------------------------------------------------------------------


class TestPathSkip:
    def test_non_mcp_path_passes_through_without_auth(self) -> None:
        validator = FakeValidator(claims=_claims())
        resolver = FakeResolver(_resolution())
        touch = FakeTouch()
        client = _build_app(validator, resolver, touch)

        resp = client.get("/healthz")  # no Authorization header
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        # Validator never invoked
        assert validator.calls == []
        assert resolver.calls == []


# --------------------------------------------------------------------------
# Missing/malformed Authorization
# --------------------------------------------------------------------------


class TestAuthorizationHeader:
    def test_missing_header_returns_401(self) -> None:
        client = _build_app(FakeValidator(claims=_claims()), FakeResolver(_resolution()), FakeTouch())
        resp = client.post("/mcp", json={})
        assert resp.status_code == 401
        assert resp.json() == {"error": "missing_token", "reason": "Bearer token required"}
        assert "WWW-Authenticate" in resp.headers
        assert "Bearer" in resp.headers["WWW-Authenticate"]
        assert _RESOURCE_METADATA in resp.headers["WWW-Authenticate"]

    def test_non_bearer_scheme_returns_401(self) -> None:
        client = _build_app(FakeValidator(claims=_claims()), FakeResolver(_resolution()), FakeTouch())
        resp = client.post("/mcp", json={}, headers={"Authorization": "Basic dXNlcjpwYXNz"})
        assert resp.status_code == 401

    def test_empty_token_returns_401(self) -> None:
        client = _build_app(FakeValidator(claims=_claims()), FakeResolver(_resolution()), FakeTouch())
        resp = client.post("/mcp", json={}, headers={"Authorization": "Bearer "})
        assert resp.status_code == 401
        assert resp.json()["reason"] == "empty token"


# --------------------------------------------------------------------------
# JWT validation failures
# --------------------------------------------------------------------------


class TestJwtFailures:
    @pytest.mark.parametrize(
        "code", ["malformed", "bad_signature", "expired", "wrong_iss", "wrong_aud", "missing_claim"]
    )
    def test_jwt_error_returns_401_with_code(self, code: str) -> None:
        validator = FakeValidator(error=JwtError(code, "test"))  # type: ignore[arg-type]
        client = _build_app(validator, FakeResolver(_resolution()), FakeTouch())
        resp = client.post("/mcp", json={}, headers={"Authorization": "Bearer x.y.z"})
        assert resp.status_code == 401
        assert resp.json()["reason"] == code
        assert code in resp.headers["WWW-Authenticate"]


# --------------------------------------------------------------------------
# Status mapping
# --------------------------------------------------------------------------


class TestStatusMapping:
    def test_active_succeeds_and_populates_context(self) -> None:
        tid, iid = uuid4(), uuid4()
        validator = FakeValidator(claims=_claims())
        resolver = FakeResolver(_resolution(tenant_id=tid, identity_id=iid))
        touch = FakeTouch()
        client = _build_app(validator, resolver, touch)
        resp = client.post("/mcp", json={}, headers={"Authorization": "Bearer x.y.z"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["tenant_id"] == str(tid)
        assert body["identity_id"] == str(iid)
        assert body["client_id"] == "client-1"
        assert body["scopes"] == ["memory.read", "memory.write"]

    def test_suspended_returns_403(self) -> None:
        validator = FakeValidator(claims=_claims())
        resolver = FakeResolver(_resolution(tenant_status="suspended"))
        client = _build_app(validator, resolver, FakeTouch())
        resp = client.post("/mcp", json={}, headers={"Authorization": "Bearer x"})
        assert resp.status_code == 403
        assert resp.json() == {"error": "account_suspended"}

    def test_pending_deletion_returns_403(self) -> None:
        validator = FakeValidator(claims=_claims())
        resolver = FakeResolver(_resolution(tenant_status="pending_deletion"))
        client = _build_app(validator, resolver, FakeTouch())
        resp = client.post("/mcp", json={}, headers={"Authorization": "Bearer x"})
        assert resp.status_code == 403
        assert resp.json() == {"error": "account_deletion_pending"}

    def test_deleted_returns_401(self) -> None:
        validator = FakeValidator(claims=_claims())
        resolver = FakeResolver(_resolution(tenant_status="deleted"))
        client = _build_app(validator, resolver, FakeTouch())
        resp = client.post("/mcp", json={}, headers={"Authorization": "Bearer x"})
        assert resp.status_code == 401
        assert resp.json()["reason"] == "tenant_deleted"

    def test_sub_not_found_returns_401(self) -> None:
        validator = FakeValidator(claims=_claims())
        resolver = FakeResolver(_resolution(tenant_status="not_found"))
        client = _build_app(validator, resolver, FakeTouch())
        resp = client.post("/mcp", json={}, headers={"Authorization": "Bearer x"})
        assert resp.status_code == 401
        assert resp.json()["reason"] == "no_tenant_for_sub"


class TestClientChecks:
    def test_unknown_client_returns_401(self) -> None:
        validator = FakeValidator(claims=_claims())
        resolver = FakeResolver(_resolution(client_known=False))
        client = _build_app(validator, resolver, FakeTouch())
        resp = client.post("/mcp", json={}, headers={"Authorization": "Bearer x"})
        assert resp.status_code == 401
        assert resp.json()["reason"] == "client_revoked_or_unknown"

    def test_disabled_client_returns_401(self) -> None:
        validator = FakeValidator(claims=_claims())
        resolver = FakeResolver(_resolution(client_disabled=True))
        client = _build_app(validator, resolver, FakeTouch())
        resp = client.post("/mcp", json={}, headers={"Authorization": "Bearer x"})
        assert resp.status_code == 401
        assert resp.json()["reason"] == "client_revoked_or_unknown"


# --------------------------------------------------------------------------
# Touch (best-effort)
# --------------------------------------------------------------------------


class TestTouch:
    @pytest.mark.asyncio
    async def test_touch_called_on_success(self) -> None:
        # We use TestClient (sync) which awaits the middleware; create_task may
        # not have run by response time. So we check via a small awaitable that
        # yields control. Easiest: use an asyncio.Event the touch sets.
        import asyncio
        seen = asyncio.Event()
        captured: list[tuple[Any, ...]] = []

        class EventTouch:
            async def touch(self, identity_id: UUID, client_id: str) -> None:
                captured.append((identity_id, client_id))
                seen.set()

        validator = FakeValidator(claims=_claims())
        tid, iid = uuid4(), uuid4()
        resolver = FakeResolver(_resolution(tenant_id=tid, identity_id=iid))

        client = _build_app(validator, resolver, EventTouch())
        resp = client.post("/mcp", json={}, headers={"Authorization": "Bearer x"})
        assert resp.status_code == 200
        # Drain the loop briefly so the create_task fires
        await asyncio.sleep(0.05)
        assert captured == [(iid, "client-1")]

    def test_touch_failure_does_not_block_request(self) -> None:
        class FailingTouch:
            async def touch(self, identity_id: UUID, client_id: str) -> None:
                raise RuntimeError("db down")

        validator = FakeValidator(claims=_claims())
        resolver = FakeResolver(_resolution())
        client = _build_app(validator, resolver, FailingTouch())
        resp = client.post("/mcp", json={}, headers={"Authorization": "Bearer x"})
        # Touch failure is fire-and-forget — request still succeeds
        assert resp.status_code == 200
