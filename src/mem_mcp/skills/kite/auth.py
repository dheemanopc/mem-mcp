"""Kite Connect authentication primitives.

The Kite Connect login flow:
1. User is redirected to https://kite.zerodha.com/connect/login?api_key=...
2. After 2FA, Zerodha redirects to our callback with ?request_token=... in the URL
3. We POST request_token + api_key + checksum to /session/token to receive access_token
4. access_token is valid until the next morning (~6 AM IST); it must be regenerated daily

Phase 1: Manual or request_token-based onboarding.
Phase 2: TOTP-based auto-refresh via stored credentials (Mode C).
"""

from __future__ import annotations

import hashlib
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import httpx
import pyotp

KITE_API_BASE_URL = "https://api.kite.trade"


class KiteAuthError(Exception):
    """Raised when Kite session-token exchange fails.

    ``step`` identifies which phase of the auto-login flow raised — useful for
    callers (algotrade etc.) that need to tell users *why* setup failed
    without grepping mem-mcp logs. Values: ``api_login`` | ``totp_generate``
    | ``twofa`` | ``connect_login`` | ``session_token`` | ``transport``.
    ``upstream_status`` carries the HTTP status from Zerodha when relevant.
    """

    def __init__(
        self,
        message: str,
        error_type: str | None = None,
        *,
        step: str | None = None,
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.step = step
        self.upstream_status = upstream_status


class KiteCredentialsError(KiteAuthError):
    """Raised when stored credentials don't allow auto-login (missing field, expired TOTP, etc.)."""

    pass


def compute_checksum(api_key: str, request_token: str, api_secret: str) -> str:
    """Per Kite spec: sha256(api_key + request_token + api_secret)."""
    s = f"{api_key}{request_token}{api_secret}"
    return hashlib.sha256(s.encode()).hexdigest()


async def exchange_request_token(
    api_key: str,
    request_token: str,
    api_secret: str,
    *,
    http: httpx.AsyncClient | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """POST /session/token to convert a request_token into an access_token.

    Returns the parsed ``data`` block: includes ``access_token``,
    ``public_token``, ``refresh_token``, ``user_id``, ``user_name``, etc.

    Raises KiteAuthError on any non-success response.

    The ``http`` param accepts an injected httpx.AsyncClient (for tests). If
    None, a transient client is created and closed within this call.
    """
    checksum = compute_checksum(api_key, request_token, api_secret)
    payload = {
        "api_key": api_key,
        "request_token": request_token,
        "checksum": checksum,
    }
    headers = {"X-Kite-Version": "3"}
    url = f"{KITE_API_BASE_URL}/session/token"

    async def _do(client: httpx.AsyncClient) -> dict[str, Any]:
        resp = await client.post(url, headers=headers, data=payload, timeout=timeout)
        try:
            body = resp.json()
        except Exception as exc:
            raise KiteAuthError(f"non-JSON response from Kite (status {resp.status_code})") from exc
        if resp.status_code != 200 or body.get("status") != "success":
            raise KiteAuthError(
                body.get("message", f"auth failed (status {resp.status_code})"),
                error_type=body.get("error_type"),
            )
        return cast(dict[str, Any], body.get("data", {}))

    if http is not None:
        return await _do(http)
    async with httpx.AsyncClient() as client:
        return await _do(client)


async def login_with_credentials(
    *,
    api_key: str,
    api_secret: str,
    user_id: str,
    password: str,
    totp_secret: str,
    http: httpx.AsyncClient | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Run the 5-step Zerodha auto-login. Returns dict with access_token, kite_user_id, public_token.

    Reference: memsys 0a0b0e1a Java implementation. Plain HTTPS, no Selenium.

    Steps:
      1. POST kite.zerodha.com/api/login (user_id, password) -> request_id
      2. Generate TOTP code from totp_secret (RFC 6238, SHA1, 6 digits, 30s window)
      3. POST kite.zerodha.com/api/twofa (user_id, request_id, twofa_value, twofa_type=totp)
      4. GET kite.zerodha.com/connect/login?api_key=... — follow redirects, parse ?request_token=
      5. POST api.kite.trade/session/token with checksum(api_key+request_token+api_secret) -> access_token
    """

    base_kite = "https://kite.zerodha.com"

    async def _do(client: httpx.AsyncClient) -> dict[str, Any]:
        # Step 1: login
        try:
            r1 = await client.post(
                f"{base_kite}/api/login",
                data={"user_id": user_id, "password": password},
                timeout=timeout,
            )
        except httpx.RequestError as exc:
            raise KiteCredentialsError(
                f"transport error on api/login: {type(exc).__name__}: {exc}",
                step="api_login",
            ) from exc
        try:
            b1 = r1.json()
        except Exception as exc:
            raise KiteCredentialsError(
                f"non-JSON response from api/login (status {r1.status_code})",
                step="api_login",
                upstream_status=r1.status_code,
            ) from exc
        if r1.status_code != 200 or b1.get("status") != "success":
            raise KiteCredentialsError(
                b1.get("message") or f"login step 1 failed (status {r1.status_code})",
                error_type=b1.get("error_type"),
                step="api_login",
                upstream_status=r1.status_code,
            )
        request_id = b1["data"].get("request_id")
        if not request_id:
            raise KiteCredentialsError(
                "login step 1: no request_id in response",
                step="api_login",
                upstream_status=r1.status_code,
            )

        # Step 2: TOTP
        try:
            twofa_value = pyotp.TOTP(totp_secret).now()
        except Exception as exc:
            raise KiteCredentialsError(
                f"invalid TOTP secret: {exc}",
                step="totp_generate",
            ) from exc

        # Step 3: 2FA
        try:
            r2 = await client.post(
                f"{base_kite}/api/twofa",
                data={
                    "user_id": user_id,
                    "request_id": request_id,
                    "twofa_value": twofa_value,
                    "twofa_type": "totp",
                },
                timeout=timeout,
            )
        except httpx.RequestError as exc:
            raise KiteCredentialsError(
                f"transport error on api/twofa: {type(exc).__name__}: {exc}",
                step="twofa",
            ) from exc
        try:
            b2 = r2.json()
        except Exception as exc:
            raise KiteCredentialsError(
                f"non-JSON response from api/twofa (status {r2.status_code})",
                step="twofa",
                upstream_status=r2.status_code,
            ) from exc
        if r2.status_code != 200 or b2.get("status") != "success":
            raise KiteCredentialsError(
                b2.get("message") or f"2FA failed (status {r2.status_code})",
                error_type=b2.get("error_type"),
                step="twofa",
                upstream_status=r2.status_code,
            )

        # Step 4: get request_token (follow redirects, parse final URL).
        # The redirect chain is:
        #   /connect/login -> /connect/finish -> <app's configured callback URL>?request_token=...
        # If the callback URL is unreachable (DNS, connect refused, timeout)
        # httpx raises a RequestError during the redirect-follow.
        try:
            r3 = await client.get(
                f"{base_kite}/connect/login",
                params={"api_key": api_key, "v": "3"},
                follow_redirects=True,
                timeout=timeout,
            )
        except httpx.TooManyRedirects as exc:
            raise KiteCredentialsError(
                "redirect chain exceeded limit during /connect/login follow",
                step="connect_login",
            ) from exc
        except httpx.RequestError as exc:
            raise KiteCredentialsError(
                f"transport error following /connect/login redirects: {type(exc).__name__}: {exc}. "
                "Check the Kite Connect app's redirect URL — it must be reachable from mem-mcp's host.",
                step="connect_login",
            ) from exc
        # The final URL should contain ?request_token=...
        parsed = urlparse(str(r3.url))
        qs = parse_qs(parsed.query)
        request_token = qs.get("request_token", [None])[0]
        if not request_token:
            raise KiteCredentialsError(
                f"could not extract request_token from final URL: {r3.url}",
                step="connect_login",
                upstream_status=r3.status_code,
            )

        # Step 5: exchange for access_token
        try:
            token_data = await exchange_request_token(
                api_key, request_token, api_secret, http=client, timeout=timeout
            )
        except KiteAuthError as exc:
            # Re-raise with step annotation so caller knows it was the last hop.
            raise KiteCredentialsError(
                str(exc),
                error_type=exc.error_type,
                step="session_token",
                upstream_status=exc.upstream_status,
            ) from exc
        except httpx.RequestError as exc:
            raise KiteCredentialsError(
                f"transport error on session/token: {type(exc).__name__}: {exc}",
                step="session_token",
            ) from exc
        return {
            "access_token": token_data.get("access_token"),
            "public_token": token_data.get("public_token"),
            "kite_user_id": token_data.get("user_id"),
        }

    if http is not None:
        return await _do(http)
    async with httpx.AsyncClient() as client:
        return await _do(client)
