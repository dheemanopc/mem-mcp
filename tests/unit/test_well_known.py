"""Tests for mem_mcp.auth.well_known (T-4.4)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mem_mcp.auth.well_known import DEFAULT_MCP_SCOPES, make_well_known_router

_RESOURCE = "https://memsys.dheemantech.in"
_COGNITO_DOMAIN = "memauth.dheemantech.in"
_REGION = "ap-south-1"
_POOL_ID = "ap-south-1_TESTPOOL"


def _build_client(**overrides: object) -> TestClient:
    args: dict[str, object] = dict(
        resource_url=_RESOURCE,
        cognito_domain=_COGNITO_DOMAIN,
        region=_REGION,
        user_pool_id=_POOL_ID,
    )
    args.update(overrides)
    app = FastAPI()
    app.include_router(make_well_known_router(**args))  # type: ignore[arg-type]
    return TestClient(app)


# --------------------------------------------------------------------------
# /.well-known/oauth-protected-resource (RFC 9728)
# --------------------------------------------------------------------------


class TestProtectedResourceMetadata:
    def test_status_and_content_type(self) -> None:
        client = _build_client()
        resp = client.get("/.well-known/oauth-protected-resource")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")

    def test_resource_field(self) -> None:
        body = _build_client().get("/.well-known/oauth-protected-resource").json()
        assert body["resource"] == _RESOURCE

    def test_authorization_servers(self) -> None:
        body = _build_client().get("/.well-known/oauth-protected-resource").json()
        assert body["authorization_servers"] == [_RESOURCE]

    def test_scopes_supported_default(self) -> None:
        body = _build_client().get("/.well-known/oauth-protected-resource").json()
        assert body["scopes_supported"] == list(DEFAULT_MCP_SCOPES)

    def test_bearer_methods(self) -> None:
        body = _build_client().get("/.well-known/oauth-protected-resource").json()
        assert body["bearer_methods_supported"] == ["header"]

    def test_resource_documentation(self) -> None:
        body = _build_client().get("/.well-known/oauth-protected-resource").json()
        assert body["resource_documentation"] == f"{_RESOURCE}/docs"


# --------------------------------------------------------------------------
# /.well-known/oauth-authorization-server (RFC 8414)
# --------------------------------------------------------------------------


class TestAuthorizationServerMetadata:
    def test_status_and_content_type(self) -> None:
        client = _build_client()
        resp = client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")

    def test_issuer(self) -> None:
        body = _build_client().get("/.well-known/oauth-authorization-server").json()
        assert body["issuer"] == _RESOURCE

    def test_authorization_endpoint(self) -> None:
        body = _build_client().get("/.well-known/oauth-authorization-server").json()
        assert body["authorization_endpoint"] == f"https://{_COGNITO_DOMAIN}/oauth2/authorize"

    def test_token_endpoint(self) -> None:
        body = _build_client().get("/.well-known/oauth-authorization-server").json()
        assert body["token_endpoint"] == f"https://{_COGNITO_DOMAIN}/oauth2/token"

    def test_jwks_uri(self) -> None:
        body = _build_client().get("/.well-known/oauth-authorization-server").json()
        assert body["jwks_uri"] == (
            f"https://cognito-idp.{_REGION}.amazonaws.com/" f"{_POOL_ID}/.well-known/jwks.json"
        )

    def test_registration_endpoint(self) -> None:
        body = _build_client().get("/.well-known/oauth-authorization-server").json()
        assert body["registration_endpoint"] == f"{_RESOURCE}/oauth/register"

    def test_scopes_supported_combines_oidc_and_mcp(self) -> None:
        body = _build_client().get("/.well-known/oauth-authorization-server").json()
        scopes = body["scopes_supported"]
        for s in ("openid", "email", "profile"):
            assert s in scopes
        for s in DEFAULT_MCP_SCOPES:
            assert s in scopes

    def test_response_and_grant_types(self) -> None:
        body = _build_client().get("/.well-known/oauth-authorization-server").json()
        assert body["response_types_supported"] == ["code"]
        assert body["grant_types_supported"] == ["authorization_code", "refresh_token"]

    def test_token_endpoint_auth_methods(self) -> None:
        body = _build_client().get("/.well-known/oauth-authorization-server").json()
        # Both 'none' (public DCR clients with PKCE) and 'client_secret_post' (web app)
        assert "none" in body["token_endpoint_auth_methods_supported"]
        assert "client_secret_post" in body["token_endpoint_auth_methods_supported"]

    def test_code_challenge_methods(self) -> None:
        body = _build_client().get("/.well-known/oauth-authorization-server").json()
        assert body["code_challenge_methods_supported"] == ["S256"]

    def test_service_documentation(self) -> None:
        body = _build_client().get("/.well-known/oauth-authorization-server").json()
        assert body["service_documentation"] == f"{_RESOURCE}/docs"


# --------------------------------------------------------------------------
# Customization
# --------------------------------------------------------------------------


class TestCustomScopes:
    def test_custom_mcp_scopes_propagate(self) -> None:
        client = _build_client(mcp_scopes=("custom.scope1", "custom.scope2"))
        prm = client.get("/.well-known/oauth-protected-resource").json()
        assert prm["scopes_supported"] == ["custom.scope1", "custom.scope2"]
        asm = client.get("/.well-known/oauth-authorization-server").json()
        assert "custom.scope1" in asm["scopes_supported"]
        assert "custom.scope2" in asm["scopes_supported"]
        # OIDC standard scopes still present
        assert "openid" in asm["scopes_supported"]
