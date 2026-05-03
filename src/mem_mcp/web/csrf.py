"""CSRF middleware (T-8.3, LLD §4.11.2).

Double-submit cookie pattern: the csrf_token cookie is JS-readable
(NOT HttpOnly); each unsafe-method request must echo the cookie value
in the X-CSRF-Token header. Mismatch -> 403.

Safe methods (GET, HEAD, OPTIONS) are not checked.
Endpoints under /api/web and /auth/logout are the protected surface.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

if TYPE_CHECKING:
    pass

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Paths the CSRF middleware GUARDS (others bypass). Adjust as web surface grows.
PROTECTED_PREFIXES = ("/api/web/", "/auth/logout")


def mint_csrf_token() -> str:
    """Generate a fresh CSRF token (32 bytes urlsafe)."""
    return secrets.token_urlsafe(32)


class CsrfMiddleware(BaseHTTPMiddleware):
    """Validates X-CSRF-Token == csrf_token cookie on unsafe methods.

    Sets the csrf_token cookie on responses if missing (so the next request can echo it back).
    """

    async def dispatch(self, request: Request, call_next: Callable[[Any], Any]) -> Response:
        """Dispatch with CSRF validation."""
        path = request.url.path
        method = request.method

        # Guard: unsafe method + protected path
        guard = method not in SAFE_METHODS and any(path.startswith(p) for p in PROTECTED_PREFIXES)

        if guard:
            cookie = request.cookies.get(CSRF_COOKIE_NAME)
            header = request.headers.get(CSRF_HEADER_NAME)

            if not cookie or not header:
                return JSONResponse(
                    {"error": "csrf_token_missing"},
                    status_code=403,
                )

            # Constant-time compare
            if not hmac.compare_digest(cookie, header):
                return JSONResponse(
                    {"error": "csrf_token_mismatch"},
                    status_code=403,
                )

        response: Response = await call_next(request)

        # Set csrf_token cookie if absent
        if CSRF_COOKIE_NAME not in request.cookies:
            response.set_cookie(
                CSRF_COOKIE_NAME,
                mint_csrf_token(),
                httponly=False,  # MUST be JS-readable for double-submit pattern
                secure=True,
                samesite="lax",
                max_age=60 * 60 * 24 * 7,  # 7d
            )

        return response
