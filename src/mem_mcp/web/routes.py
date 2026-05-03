"""Web routes for the Next.js shell (T-8.2, LLD §6.1/§6.2).

Endpoints:
- GET  /auth/login      — 302 to Cognito Hosted UI with state
- GET  /auth/callback   — token exchange + session create OR link complete
- POST /auth/logout     — revoke session

The Next.js app (T-8.4+) renders the actual pages; this module
exposes only the auth glue + logout.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Request
from fastapi.responses import RedirectResponse, Response

from mem_mcp.db.tenant_tx import system_tx
from mem_mcp.web.sessions import (
    SESSION_COOKIE_NAME,
    SESSION_TTL,
    create_session,
    revoke_session,
)

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


class CognitoTokens(Protocol):
    """Tokens returned from Cognito token endpoint."""

    @property
    def access_token(self) -> str: ...

    @property
    def id_token(self) -> str: ...

    @property
    def refresh_token(self) -> str: ...

    @property
    def expires_in(self) -> int: ...


class CognitoUserInfo(Protocol):
    """User info extracted from Cognito id_token."""

    @property
    def cognito_sub(self) -> str: ...

    @property
    def cognito_username(self) -> str: ...

    @property
    def email(self) -> str: ...

    @property
    def provider(self) -> str: ...

    @property
    def provider_user_id(self) -> str: ...


class CognitoTokenExchanger(Protocol):
    """Wraps the Cognito Hosted UI token endpoint."""

    async def exchange_code(self, code: str, redirect_uri: str) -> CognitoTokens: ...


class CognitoUserInfoFetcher(Protocol):
    """Pulls userinfo from the id_token."""

    async def get_user_info(self, id_token: str) -> CognitoUserInfo: ...


def make_web_router(
    *,
    cognito_authorize_base_url: str,
    cognito_client_id: str,
    callback_url: str,
    token_exchanger: CognitoTokenExchanger,
    user_info_fetcher: CognitoUserInfoFetcher,
    pool: asyncpg.Pool,
    audit: Any,
) -> APIRouter:
    """Builds the /auth router. Returns the FastAPI APIRouter.

    Args:
        cognito_authorize_base_url: e.g. https://memsys-staging.auth.ap-south-1.amazoncognito.com/oauth2/authorize
        cognito_client_id: Cognito User Pool client ID
        callback_url: redirect_uri registered in Cognito (e.g. https://memsys.dheemantech.in/auth/callback)
        token_exchanger: CognitoTokenExchanger Protocol (wraps /oauth2/token endpoint)
        user_info_fetcher: CognitoUserInfoFetcher Protocol (extracts claims from id_token)
        pool: asyncpg.Pool for DB operations
        audit: AuditLogger Protocol for audit events
    """
    router = APIRouter()

    @router.get("/auth/login")
    async def login(request: Request) -> Response:
        """Redirect to Cognito Hosted UI with state.

        NOTE: PKCE wiring deferred to v2. This v1 flow uses state cookie only.
        The state cookie is stored in the response; on callback, we verify it
        matches the state parameter from Cognito.
        """
        state = secrets.token_urlsafe(32)
        params = {
            "client_id": cognito_client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": callback_url,
            "state": state,
        }
        response = RedirectResponse(
            f"{cognito_authorize_base_url}?{urlencode(params)}", status_code=302
        )
        # Store state in a short-lived cookie for verification on callback
        response.set_cookie(
            "auth_state",
            state,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=600,  # 10 minutes
        )
        return response

    @router.get("/auth/callback")
    async def callback(request: Request, code: str, state: str) -> Response:
        """Token exchange + existing-user lookup → session + dashboard redirect.

        Flow:
        1. Verify state cookie matches state param
        2. Exchange code for tokens
        3. Look up tenant_identities by cognito_sub
           - HIT → existing user; create web session; redirect /dashboard
           - MISS → new user or linking flow. Log warning and redirect /welcome
        """
        # Verify state
        state_cookie = request.cookies.get("auth_state")
        if not state_cookie or state_cookie != state:
            # State mismatch: log and redirect to welcome
            return RedirectResponse("/welcome?status=invalid_state", status_code=302)

        # Token exchange
        try:
            tokens = await token_exchanger.exchange_code(code, callback_url)
            info = await user_info_fetcher.get_user_info(tokens.id_token)
        except Exception:
            # Token exchange failure: redirect to welcome
            return RedirectResponse("/welcome?status=token_exchange_failed", status_code=302)

        # Look up tenant_identities by cognito_sub
        async with system_tx(pool) as conn:
            row = await conn.fetchrow(
                """
                SELECT tenant_id, id
                FROM tenant_identities
                WHERE cognito_sub = $1
                """,
                info.cognito_sub,
            )

        if row is None:
            # New user. In v1, the tenant is created by PreSignUp Lambda (T-4.8).
            # If we get here and tenant doesn't exist, something went wrong.
            # Linking flow is handled via separate /api/web/identities/link/complete
            # endpoint (T-8.9). Log warning and redirect to welcome.
            return RedirectResponse("/welcome?status=tenant_not_provisioned", status_code=302)

        # Create web session
        ua = request.headers.get("user-agent")
        ip = request.client.host if request.client else None
        result = await create_session(
            pool,
            tenant_id=row["tenant_id"],
            identity_id=row["id"],
            user_agent=ua,
            ip_address=ip,
        )

        response = RedirectResponse("/dashboard", status_code=302)
        response.set_cookie(
            SESSION_COOKIE_NAME,
            result.raw_value,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=int(SESSION_TTL.total_seconds()),
        )
        # Clear the state cookie
        response.delete_cookie("auth_state")
        return response

    @router.post("/auth/logout")
    async def logout(request: Request, mem_session: str | None = Cookie(default=None)) -> Response:
        """Revoke session and clear cookie."""
        if mem_session:
            await revoke_session(pool, mem_session)

        response = RedirectResponse("/", status_code=302)
        response.delete_cookie(SESSION_COOKIE_NAME)
        return response

    return router
