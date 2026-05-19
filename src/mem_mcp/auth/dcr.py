"""Dynamic Client Registration (RFC 7591) shim — POST /oauth/register.

AI clients (Claude Code, Claude.ai, ChatGPT) hit this before they can
authenticate. We:
  - Validate the request shape (Pydantic + redirect_uri scheme rules)
  - Enforce rate limits (per-IP 5/h, global 100/day)
  - Check allowed_software allowlist (FR-6.5.7)
  - Create the actual client in Cognito (SupportedIdentityProviders=['Google'])
  - Persist to oauth_clients with sha256(registration_access_token)
  - Return the RFC 7591 envelope to the caller

Per spec §6.5 + LLD §4.3.3. Audit logging deferred to T-5.12.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.responses import JSONResponse

from mem_mcp.auth.well_known import DCR_ALLOWED_SCOPES
from mem_mcp.db import system_tx
from mem_mcp.logging_setup import get_logger

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

_log = get_logger("mem_mcp.auth.dcr")

# Limits
MAX_REQUEST_BYTES = 8 * 1024
MAX_REDIRECT_URIS = 5
PER_IP_LIMIT = 5
PER_IP_WINDOW_SECONDS = 3600  # 1 hour
GLOBAL_LIMIT = 100
GLOBAL_WINDOW_SECONDS = 86400  # 1 day

_COGNITO_CLIENT_NAME_RE = re.compile(r"[^\w\s+=,.@-]+")
_LOCALHOST_HOSTS = ("localhost", "127.0.0.1")


# v1 delta: SupportedIdentityProviders is Google-only (LLD §0)
_SUPPORTED_IDPS = ("Google",)

AllowedSoftwareStatus = Literal["allowed", "blocked", "pending_review", "revoked", "unknown"]


@dataclass(frozen=True)
class AllowedSoftwarePolicy:
    """Result of looking up an allowed_software row. status='unknown' for rows that don't exist."""

    status: AllowedSoftwareStatus
    require_secret: bool = False
    allowed_redirect_uris: list[str] | None = None  # None == any URI allowed


# Map of client_name (lowercased, trimmed) → canonical software_id, used when
# the DCR request omits `software_id` (Claude.ai and many other MCP clients
# don't send it, even though we still want to gate via allowed_software).
_CLIENT_NAME_TO_SOFTWARE_ID = {
    "claude": "claude-ai",
    "claude.ai": "claude-ai",
    "claude code": "claude-code",
    "chatgpt": "chatgpt",
    "cursor": "cursor",
    "perplexity": "perplexity",
}


def _derive_software_id(client_name: str) -> str:
    """Best-effort derivation of software_id from client_name.

    Tries the explicit alias map first; falls back to a slugified client_name
    so the allowed_software lookup still produces a deterministic result
    (which will then be allowed/blocked by the operator-curated allowlist).
    """
    key = client_name.strip().lower()
    if key in _CLIENT_NAME_TO_SOFTWARE_ID:
        return _CLIENT_NAME_TO_SOFTWARE_ID[key]
    return key.replace(" ", "-").replace(".", "-")


# --------------------------------------------------------------------------
# Pydantic models
# --------------------------------------------------------------------------


class DcrInput(BaseModel):
    """RFC 7591 request body."""

    model_config = ConfigDict(extra="ignore")

    client_name: str = Field(..., max_length=128)
    client_uri: str | None = None
    redirect_uris: list[str] = Field(..., min_length=1, max_length=MAX_REDIRECT_URIS)
    grant_types: list[str] = Field(
        default=["authorization_code", "refresh_token"],
    )
    response_types: list[str] = Field(default=["code"])
    token_endpoint_auth_method: Literal["none", "client_secret_post"] = "none"
    scope: str = "memory.read memory.write"
    software_id: str | None = Field(default=None, max_length=128)
    software_version: str | None = Field(default=None, max_length=64)

    @field_validator("redirect_uris")
    @classmethod
    def _validate_redirect_uris(cls, uris: list[str]) -> list[str]:
        for uri in uris:
            parsed = urlparse(uri)
            if parsed.fragment:
                raise ValueError(f"redirect_uri must not have fragment: {uri!r}")
            if "*" in uri:
                raise ValueError(f"redirect_uri must not contain wildcards: {uri!r}")
            if parsed.scheme == "https":
                continue
            if parsed.scheme == "http" and parsed.hostname in _LOCALHOST_HOSTS:
                continue
            raise ValueError(
                f"redirect_uri must be https://, http://localhost, or http://127.0.0.1: {uri!r}"
            )
        return uris

    @field_validator("grant_types")
    @classmethod
    def _validate_grant_types(cls, grants: list[str]) -> list[str]:
        allowed = {"authorization_code", "refresh_token"}
        for g in grants:
            if g not in allowed:
                raise ValueError(f"unknown grant_type {g!r}; allowed: {sorted(allowed)}")
        return grants

    @field_validator("response_types")
    @classmethod
    def _validate_response_types(cls, responses: list[str]) -> list[str]:
        allowed = {"code"}
        for r in responses:
            if r not in allowed:
                raise ValueError(f"unknown response_type {r!r}; allowed: {sorted(allowed)}")
        return responses

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, scope: str) -> str:
        if not scope.split():
            raise ValueError("scope must contain at least one entry")
        return scope


class DcrOutput(BaseModel):
    """RFC 7591 successful registration response."""

    client_id: str
    client_id_issued_at: int
    client_secret: str | None = None  # NEW — present for confidential clients
    client_secret_expires_at: int = 0  # public client = no secret = never expires (per RFC 7591)
    redirect_uris: list[str]
    grant_types: list[str]
    response_types: list[str]
    token_endpoint_auth_method: str
    scope: str
    registration_access_token: str
    registration_client_uri: str


# --------------------------------------------------------------------------
# Protocols (test seams)
# --------------------------------------------------------------------------


class CognitoClientFactory(Protocol):
    async def create_user_pool_client(
        self,
        *,
        client_name: str,
        callback_urls: list[str],
        scopes: list[str],
        generate_secret: bool = False,
    ) -> tuple[str, str | None]:
        """Return (client_id, client_secret_or_None)."""
        ...


class OauthClientStore(Protocol):
    async def insert(
        self,
        *,
        client_id: str,
        software_id: str,
        client_name: str,
        redirect_uris: list[str],
        scope: str,
        registration_payload: dict[str, Any],
        registration_access_token_hash: str,
    ) -> None: ...


class AllowedSoftwareLookup(Protocol):
    async def policy(self, software_id: str) -> AllowedSoftwarePolicy: ...


class RateLimiter(Protocol):
    async def check_and_consume(self, key: str, limit: int, window_seconds: int) -> bool: ...


# --------------------------------------------------------------------------
# Production implementations
# --------------------------------------------------------------------------


class BotoCognitoClientFactory:
    """Production CognitoClientFactory using boto3 cognito-idp."""

    # Standard OIDC scopes that are NOT prefixed with the resource server identifier
    _OIDC_SCOPES = frozenset({"openid", "email", "profile", "phone", "address"})

    def __init__(
        self,
        user_pool_id: str,
        region: str,
        resource_server_identifier: str | None = None,
        supported_idps: tuple[str, ...] = _SUPPORTED_IDPS,
    ) -> None:
        self.user_pool_id = user_pool_id
        self.region = region
        self.resource_server_identifier = resource_server_identifier
        self.supported_idps = supported_idps

    def _qualify_scopes(self, scopes: list[str]) -> list[str]:
        """Prefix custom scopes with the resource server identifier.

        Cognito requires custom scopes to be referenced as
        `<resource_server_identifier>/<scope_name>` (e.g.,
        `https://memsys.dheemantech.in/memory.read`). Standard OIDC scopes
        are passed through unchanged. If no identifier is configured, scopes
        are passed through unchanged (preserves test fakes).
        """
        if not self.resource_server_identifier:
            return list(scopes)
        prefix = self.resource_server_identifier.rstrip("/")
        out: list[str] = []
        for s in scopes:
            if s in self._OIDC_SCOPES or "/" in s:
                out.append(s)
            else:
                out.append(f"{prefix}/{s}")
        return out

    async def create_user_pool_client(
        self,
        *,
        client_name: str,
        callback_urls: list[str],
        scopes: list[str],
        generate_secret: bool = False,
    ) -> tuple[str, str | None]:
        # Lazy import keeps boto3 cost out of test paths
        import asyncio

        import boto3  # type: ignore[import-untyped]

        qualified = self._qualify_scopes(scopes)

        def _call() -> tuple[str, str | None]:
            client = boto3.client("cognito-idp", region_name=self.region)
            response = client.create_user_pool_client(
                UserPoolId=self.user_pool_id,
                ClientName=client_name,
                AllowedOAuthFlows=["code"],
                AllowedOAuthFlowsUserPoolClient=True,
                AllowedOAuthScopes=qualified,
                CallbackURLs=callback_urls,
                SupportedIdentityProviders=list(self.supported_idps),
                GenerateSecret=generate_secret,
                EnableTokenRevocation=True,
            )
            user_pool_client = response["UserPoolClient"]
            client_id = str(user_pool_client["ClientId"])
            client_secret = user_pool_client.get("ClientSecret")
            return client_id, (str(client_secret) if client_secret else None)

        return await asyncio.to_thread(_call)


class DbOauthClientStore:
    """Production OauthClientStore using asyncpg + system_tx."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert(
        self,
        *,
        client_id: str,
        software_id: str,
        client_name: str,
        redirect_uris: list[str],
        scope: str,
        registration_payload: dict[str, Any],
        registration_access_token_hash: str,
    ) -> None:
        import json

        async with system_tx(self._pool) as conn:
            await conn.execute(
                """
                INSERT INTO oauth_clients (
                    id, software_id, client_name, redirect_uris, scope,
                    registration_payload, registration_access_token_hash,
                    review_status, disabled
                ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, 'auto_allowed', false)
                """,
                client_id,
                software_id,
                client_name,
                redirect_uris,
                scope,
                json.dumps(registration_payload),
                registration_access_token_hash,
            )


class DbAllowedSoftwareLookup:
    """Production AllowedSoftwareLookup using asyncpg + system_tx."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def policy(self, software_id: str) -> AllowedSoftwarePolicy:
        async with system_tx(self._pool) as conn:
            row = await conn.fetchrow(
                "SELECT status, require_secret, allowed_redirect_uris "
                "FROM allowed_software WHERE software_id = $1",
                software_id,
            )
        if row is None:
            return AllowedSoftwarePolicy(status="unknown")
        return AllowedSoftwarePolicy(
            status=row["status"],
            require_secret=bool(row["require_secret"]),
            allowed_redirect_uris=list(row["allowed_redirect_uris"])
            if row["allowed_redirect_uris"] is not None
            else None,
        )


@dataclass
class _Bucket:
    count: int
    window_start: float


class InMemoryRateLimiter:
    """Per-process token bucket. v1 acceptable per LLD §4.8 — with 2 uvicorn workers,
    effective limit is 2x declared. Acceptable for closed beta.
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._clock = clock or time.monotonic

    async def check_and_consume(self, key: str, limit: int, window_seconds: int) -> bool:
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None or (now - bucket.window_start) >= window_seconds:
            self._buckets[key] = _Bucket(count=1, window_start=now)
            return True
        if bucket.count >= limit:
            return False
        bucket.count += 1
        return True


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _sanitize_client_name(name: str) -> str:
    """Replace any char outside [\\w\\s+=,.@-] with '-' (FR-6.5.8)."""
    return _COGNITO_CLIENT_NAME_RE.sub("-", name)[:128]


def _mint_registration_access_token() -> tuple[str, str]:
    """Return (plaintext_token, sha256_hex_hash)."""
    plaintext = secrets.token_urlsafe(32)
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return plaintext, digest


def _client_ip(request: Request) -> str:
    """Extract client IP. Behind Caddy reverse proxy; trust X-Forwarded-For if present."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------


def make_dcr_router(
    *,
    cognito_factory: CognitoClientFactory,
    client_store: OauthClientStore,
    software_lookup: AllowedSoftwareLookup,
    rate_limiter: RateLimiter,
    resource_url: str,
) -> APIRouter:
    """Build the /oauth/register router."""
    router = APIRouter(tags=["dcr"])

    @router.post("/oauth/register", status_code=status.HTTP_201_CREATED)
    async def register(request: Request) -> JSONResponse:
        # FR-6.5.1: 8 KB request limit
        try:
            content_length = int(request.headers.get("content-length", "0"))
        except ValueError:
            content_length = 0
        if content_length > MAX_REQUEST_BYTES:
            raise HTTPException(
                status_code=413,
                detail={
                    "error": "invalid_request",
                    "error_description": "request body exceeds 8 KB",
                },
            )

        # Parse body via Pydantic (handles FR-6.5.2..6.5.6, 6.5.8)
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_request", "error_description": "body is not JSON"},
            ) from None

        try:
            payload = DcrInput.model_validate(body)
        except Exception as exc:  # pydantic ValidationError
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_request", "error_description": str(exc)[:500]},
            ) from exc

        # FR-6.5.9: rate limit (per-IP first, then global)
        ip = _client_ip(request)
        if not await rate_limiter.check_and_consume(
            f"dcr:ip:{ip}", PER_IP_LIMIT, PER_IP_WINDOW_SECONDS
        ):
            raise HTTPException(
                status_code=429,
                detail={"error": "too_many_requests", "scope": "per-ip"},
                headers={"Retry-After": str(PER_IP_WINDOW_SECONDS)},
            )
        if not await rate_limiter.check_and_consume(
            "dcr:global", GLOBAL_LIMIT, GLOBAL_WINDOW_SECONDS
        ):
            raise HTTPException(
                status_code=429,
                detail={"error": "too_many_requests", "scope": "global"},
                headers={"Retry-After": str(GLOBAL_WINDOW_SECONDS)},
            )

        # FR-6.5.7: allowlist check.
        # Many MCP clients (Claude.ai included) omit software_id in DCR; derive
        # a canonical id from client_name so the allowlist still gates access.
        effective_software_id = payload.software_id or _derive_software_id(payload.client_name)
        policy = await software_lookup.policy(effective_software_id)
        if policy.status != "allowed":
            # TODO(T-5.12): audit oauth.dcr_rejected
            _log.warning(
                "dcr_rejected",
                software_id=effective_software_id,
                status=policy.status,
                ip=ip,
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized_client",
                    "error_description": f"software_id {effective_software_id!r} status={policy.status}",
                },
            )

        # Confidential clients: must request client_secret_post
        if policy.require_secret and payload.token_endpoint_auth_method != "client_secret_post":
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_client_metadata",
                    "error_description": (
                        f"software_id {effective_software_id!r} is a confidential client; "
                        "token_endpoint_auth_method must be 'client_secret_post'"
                    ),
                },
            )

        # Redirect URI allowlist (per-software, if configured)
        if policy.allowed_redirect_uris is not None:
            allow = set(policy.allowed_redirect_uris)
            for uri in payload.redirect_uris:
                if uri not in allow:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": "invalid_redirect_uri",
                            "error_description": (
                                f"redirect_uri {uri!r} not in allowlist for software_id "
                                f"{effective_software_id!r}"
                            ),
                        },
                    )

        # FR-6.5.8: sanitize client name
        sanitized_name = _sanitize_client_name(payload.client_name)

        # Scope handling for DCR-registered clients.
        #
        # memory.admin and account.manage are intentionally NOT in DCR_ALLOWED_SCOPES;
        # they require session-cookie auth (web UI) or the future admin console.
        # The well-known/oauth-authorization-server endpoint still advertises all four
        # (they exist on the resource server) which means generic MCP clients —
        # Claude Code, ChatGPT, Cursor — will read discovery and request the full
        # set in DCR. That's their correct behavior; ours is to not break them.
        #
        # Tier-1 (public, require_secret=False) — be liberal: silently intersect with
        # DCR_ALLOWED_SCOPES so blind discovery-driven clients still register. They
        # always get the full DCR-allowed set regardless of what was asked.
        #
        # Tier-2 (confidential, require_secret=True) — algotrade and future partners —
        # be strict: reject any out-of-allowlist scope with 400 invalid_scope.
        # Confidential clients are deliberate integrations; opt into each scope.
        requested = set(payload.scope.split())
        if policy.require_secret:
            unknown = requested - set(DCR_ALLOWED_SCOPES)
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "invalid_scope",
                        "error_description": (
                            f"scopes not allowed for DCR clients: {sorted(unknown)}. "
                            f"Allowed: {list(DCR_ALLOWED_SCOPES)}"
                        ),
                    },
                )
            effective_scopes = sorted(requested) if requested else list(DCR_ALLOWED_SCOPES)
        else:
            # Tier-1: silently downgrade — any out-of-allowlist scope is dropped.
            effective_scopes = sorted(DCR_ALLOWED_SCOPES)

        # Create in Cognito
        try:
            (
                cognito_client_id,
                cognito_client_secret,
            ) = await cognito_factory.create_user_pool_client(
                client_name=sanitized_name,
                callback_urls=payload.redirect_uris,
                scopes=effective_scopes,
                generate_secret=policy.require_secret,
            )
        except Exception as exc:
            # Catch all exceptions from boto3 API call
            _log.error("cognito_create_user_pool_client_failed", error=str(exc)[:300])
            raise HTTPException(
                status_code=500,
                detail={"error": "server_error", "error_description": "registration failed"},
            ) from exc

        # Mint registration_access_token
        plaintext_token, token_hash = _mint_registration_access_token()

        # Persist
        await client_store.insert(
            client_id=cognito_client_id,
            software_id=effective_software_id,
            client_name=sanitized_name,
            redirect_uris=payload.redirect_uris,
            scope=" ".join(effective_scopes),
            registration_payload=payload.model_dump(),
            registration_access_token_hash=token_hash,
        )

        # TODO(T-5.12): audit oauth.dcr_register

        # Qualify custom scopes in the response so Claude builds the correct
        # authorization URL (Cognito requires <resource_server_id>/<scope>).
        _OIDC = frozenset({"openid", "email", "profile", "phone", "address"})  # noqa: N806
        _prefix = resource_url.rstrip("/")
        qualified_scope = " ".join(
            s if (s in _OIDC or "/" in s) else f"{_prefix}/{s}" for s in effective_scopes
        )

        response_body = {
            "client_id": cognito_client_id,
            "client_id_issued_at": int(time.time()),
            "client_secret_expires_at": 0,
            "redirect_uris": payload.redirect_uris,
            "grant_types": payload.grant_types,
            "response_types": payload.response_types,
            "token_endpoint_auth_method": (
                "client_secret_post" if cognito_client_secret else "none"
            ),
            "scope": qualified_scope,
            "registration_access_token": plaintext_token,
            "registration_client_uri": f"{resource_url.rstrip('/')}/oauth/register/{cognito_client_id}",
        }
        if cognito_client_secret:
            response_body["client_secret"] = cognito_client_secret

        return JSONResponse(content=response_body, status_code=status.HTTP_201_CREATED)

    return router
