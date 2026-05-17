"""Kite Connect authentication primitives.

The Kite Connect login flow:
1. User is redirected to https://kite.zerodha.com/connect/login?api_key=...
2. After 2FA, Zerodha redirects to our callback with ?request_token=... in the URL
3. We POST request_token + api_key + checksum to /session/token to receive access_token
4. access_token is valid until the next morning (~6 AM IST); it must be regenerated daily

Phase 1: TOTP-based auto-refresh is NOT in scope. Tenant must supply a fresh
access_token via the onboarding flow each day until automation is added.
"""

from __future__ import annotations

import hashlib

import httpx

KITE_API_BASE_URL = "https://api.kite.trade"


class KiteAuthError(Exception):
    """Raised when Kite session-token exchange fails."""

    def __init__(self, message: str, error_type: str | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type


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
) -> dict:
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

    async def _do(client: httpx.AsyncClient) -> dict:
        resp = await client.post(url, headers=headers, data=payload, timeout=timeout)
        try:
            body = resp.json()
        except Exception as exc:
            raise KiteAuthError(
                f"non-JSON response from Kite (status {resp.status_code})"
            ) from exc
        if resp.status_code != 200 or body.get("status") != "success":
            raise KiteAuthError(
                body.get("message", f"auth failed (status {resp.status_code})"),
                error_type=body.get("error_type"),
            )
        return body.get("data", {})

    if http is not None:
        return await _do(http)
    async with httpx.AsyncClient() as client:
        return await _do(client)
