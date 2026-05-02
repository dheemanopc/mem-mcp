"""OAuth 2.0 protected-resource (RFC 9728) + authorization-server (RFC 8414) metadata.

Both endpoints are public (no auth) — clients fetch them as part of OAuth
discovery before they have any credentials. The Bearer middleware skips
non-/mcp paths so these pass through unauthenticated.

The shapes are constants modulo a few values plugged in from settings:
  - resource_url           e.g. https://memsys.dheemantech.in
  - cognito_domain         e.g. memauth.dheemantech.in
  - region                 e.g. ap-south-1
  - user_pool_id           e.g. ap-south-1_TESTPOOL

Per spec §6.3 (PRM) and §6.4 (AS metadata).
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter

DEFAULT_MCP_SCOPES: tuple[str, ...] = (
    "memory.read",
    "memory.write",
    "memory.admin",
    "account.manage",
)

# OIDC-standard scopes that Cognito always supports for federated identity flows
_STANDARD_OIDC_SCOPES: tuple[str, ...] = ("openid", "email", "profile")


def make_well_known_router(
    *,
    resource_url: str,
    cognito_domain: str,
    region: str,
    user_pool_id: str,
    mcp_scopes: Sequence[str] = DEFAULT_MCP_SCOPES,
) -> APIRouter:
    """Build the .well-known/* router for OAuth metadata discovery.

    Caller wires the result into the FastAPI app:
        app.include_router(make_well_known_router(...))
    """
    router = APIRouter(tags=["well-known"])

    prm_payload: dict[str, object] = {
        "resource": resource_url,
        "authorization_servers": [resource_url],
        "scopes_supported": list(mcp_scopes),
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{resource_url}/docs",
    }

    asm_payload: dict[str, object] = {
        "issuer": resource_url,
        "authorization_endpoint": f"https://{cognito_domain}/oauth2/authorize",
        "token_endpoint": f"https://{cognito_domain}/oauth2/token",
        "jwks_uri": (
            f"https://cognito-idp.{region}.amazonaws.com/" f"{user_pool_id}/.well-known/jwks.json"
        ),
        "registration_endpoint": f"{resource_url}/oauth/register",
        "scopes_supported": list(_STANDARD_OIDC_SCOPES) + list(mcp_scopes),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "code_challenge_methods_supported": ["S256"],
        "service_documentation": f"{resource_url}/docs",
    }

    @router.get("/.well-known/oauth-protected-resource")
    async def protected_resource_metadata() -> dict[str, object]:
        return prm_payload

    @router.get("/.well-known/oauth-authorization-server")
    async def authorization_server_metadata() -> dict[str, object]:
        return asm_payload

    return router
