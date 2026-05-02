"""mem_mcp authentication layer (JWKS, JWT validation, OAuth shim).

Modules land incrementally per Phase 4:
- jwks (T-4.1, this PR)
- jwt_validator (T-4.2)
- middleware (T-4.3)
- well_known (T-4.4)
- dcr / dcr_admin (T-4.5 / T-4.6)
- internal_invite (T-4.7)
"""

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
]
