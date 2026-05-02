"""mem_mcp authentication layer (JWKS, JWT validation, OAuth shim).

Modules land incrementally per Phase 4:
- jwks (T-4.1, this PR)
- jwt_validator (T-4.2)
- middleware (T-4.3)
- well_known (T-4.4)
- dcr / dcr_admin (T-4.5 / T-4.6)
- internal_invite (T-4.7)
"""

from mem_mcp.auth.dcr import (
    AllowedSoftwareLookup,
    BotoCognitoClientFactory,
    CognitoClientFactory,
    DbAllowedSoftwareLookup,
    DbOauthClientStore,
    DcrInput,
    DcrOutput,
    InMemoryRateLimiter,
    OauthClientStore,
    RateLimiter,
    make_dcr_router,
)
from mem_mcp.auth.dcr_admin import (
    BotoCognitoClientDeleter,
    CognitoClientDeleter,
    DbOauthClientDeleter,
    DbOauthClientLookup,
    OauthClientDeleter,
    OauthClientLookup,
    make_dcr_admin_router,
)
from mem_mcp.auth.jwks import (
    HttpxJwksFetcher,
    JwkKey,
    JwksCache,
    JwksError,
    JwksFetcher,
    JwksPayload,
)
from mem_mcp.auth.jwt_validator import (
    JwtClaims,
    JwtError,
    JwtValidator,
)
from mem_mcp.auth.middleware import (
    DbTenantResolver,
    DbTouch,
    TenantContext,
    TenantResolution,
    TenantResolver,
    TouchSink,
    make_bearer_middleware,
)
from mem_mcp.auth.well_known import (
    DEFAULT_MCP_SCOPES,
    make_well_known_router,
)

__all__ = [
    "HttpxJwksFetcher",
    "JwkKey",
    "JwksCache",
    "JwksError",
    "JwksFetcher",
    "JwksPayload",
    "JwtClaims",
    "JwtError",
    "JwtValidator",
    "DbTenantResolver",
    "DbTouch",
    "TenantContext",
    "TenantResolution",
    "TenantResolver",
    "TouchSink",
    "make_bearer_middleware",
    "DEFAULT_MCP_SCOPES",
    "make_well_known_router",
    "AllowedSoftwareLookup",
    "BotoCognitoClientFactory",
    "CognitoClientFactory",
    "DbAllowedSoftwareLookup",
    "DbOauthClientStore",
    "DcrInput",
    "DcrOutput",
    "InMemoryRateLimiter",
    "OauthClientStore",
    "RateLimiter",
    "make_dcr_router",
    "BotoCognitoClientDeleter",
    "CognitoClientDeleter",
    "DbOauthClientDeleter",
    "DbOauthClientLookup",
    "OauthClientDeleter",
    "OauthClientLookup",
    "make_dcr_admin_router",
]
