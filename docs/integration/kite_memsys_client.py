"""Portable memsys client for a headless partner system (e.g. the kite
trading system).

Drop this file into your kite repo. It is intentionally self-contained —
one dependency (``httpx``) — and speaks the memsys ``POST /mcp`` JSON-RPC
2.0 protocol with a Cognito Bearer token that it mints itself.

Two auth modes, selected by ``MEMSYS_AUTH_MODE``:

  * ``client_credentials`` (PROD) — the machine app client
    (``mem-kite-service-client``) exchanges its id+secret for an access
    token. No human, self-renewing. Requires the service tenant to be
    seeded first (scripts/seed-service-tenant.sh in the memsys repo).

  * ``local_admin`` (LOCAL) — cognito-local does not implement
    client_credentials, so locally we use the same ADMIN_USER_PASSWORD_AUTH
    flow as scripts/dev-token.sh (admin@local). Scope checks are bypassed
    locally, so no memory scopes are needed there.

Usage:

    from kite_memsys_client import from_env
    mem = from_env()
    mem.memory_write("NIFTY breakout entry @ 23480, SL 23410",
                     type="note", tags=["kite", "trade"])
    hits = mem.memory_search("nifty breakout entries", limit=5)

Errors are HTTP 200 with a JSON-RPC ``error`` body — this client raises
``MemsysToolError`` for those, and ``MemsysAuthError`` for 401s.
"""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any, Protocol

import httpx


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class MemsysError(Exception):
    """Base for all memsys client errors."""


class MemsysAuthError(MemsysError):
    """Token acquisition failed, or /mcp returned 401."""


class MemsysToolError(MemsysError):
    """A tool call returned a JSON-RPC error envelope."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.data = data
        super().__init__(f"[{code}] {message} :: {data!r}")


# --------------------------------------------------------------------------
# Token providers
# --------------------------------------------------------------------------
class TokenProvider(Protocol):
    def get_token(self) -> str: ...


class ClientCredentialsTokenProvider:
    """PROD: OAuth2 client_credentials against the Cognito hosted domain.

    Caches the token and refreshes ``leeway`` seconds before expiry so a
    long-running process never presents a stale token.
    """

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        scopes: str,
        http: httpx.Client | None = None,
        leeway_seconds: int = 300,
        clock=time.time,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = scopes
        self._http = http or httpx.Client(timeout=10.0)
        self._leeway = leeway_seconds
        self._clock = clock
        self._token: str | None = None
        self._expires_at: float = 0.0

    def get_token(self) -> str:
        if self._token is not None and self._clock() < self._expires_at - self._leeway:
            return self._token
        try:
            resp = self._http.post(
                self._token_url,
                auth=(self._client_id, self._client_secret),
                data={"grant_type": "client_credentials", "scope": self._scopes},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPError as exc:
            raise MemsysAuthError(f"client_credentials token request failed: {exc}") from exc
        token = body.get("access_token")
        if not token:
            raise MemsysAuthError(f"no access_token in token response: {body!r}")
        self._token = token
        self._expires_at = self._clock() + float(body.get("expires_in", 3600))
        return token

    def invalidate(self) -> None:
        """Force a refetch on next get_token() (called after a 401)."""
        self._token = None
        self._expires_at = 0.0


class LocalAdminTokenProvider:
    """LOCAL: cognito-local ADMIN_USER_PASSWORD_AUTH (mirrors dev-token.sh)."""

    def __init__(
        self,
        cognito_local_url: str,
        pool_id: str,
        client_id: str,
        username: str,
        password: str,
        http: httpx.Client | None = None,
    ) -> None:
        self._url = cognito_local_url.rstrip("/") + "/"
        self._pool_id = pool_id
        self._client_id = client_id
        self._username = username
        self._password = password
        self._http = http or httpx.Client(timeout=10.0)
        self._token: str | None = None
        self._expires_at: float = 0.0

    def get_token(self) -> str:
        if self._token is not None and time.time() < self._expires_at - 60:
            return self._token
        try:
            resp = self._http.post(
                self._url,
                headers={
                    "Content-Type": "application/x-amz-json-1.1",
                    "X-Amz-Target": "AWSCognitoIdentityProviderService.AdminInitiateAuth",
                },
                content=json.dumps(
                    {
                        "UserPoolId": self._pool_id,
                        "ClientId": self._client_id,
                        "AuthFlow": "ADMIN_USER_PASSWORD_AUTH",
                        "AuthParameters": {
                            "USERNAME": self._username,
                            "PASSWORD": self._password,
                        },
                    }
                ),
            )
            resp.raise_for_status()
            result = resp.json()["AuthenticationResult"]
        except (httpx.HTTPError, KeyError) as exc:
            raise MemsysAuthError(f"cognito-local admin auth failed: {exc}") from exc
        self._token = result["AccessToken"]
        self._expires_at = time.time() + float(result.get("ExpiresIn", 3600))
        return self._token

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = 0.0


# --------------------------------------------------------------------------
# MCP client
# --------------------------------------------------------------------------
class MemsysClient:
    """Calls memsys tools over the JSON-RPC 2.0 ``POST /mcp`` endpoint."""

    def __init__(
        self,
        base_url: str,
        token_provider: TokenProvider,
        http: httpx.Client | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._url = base_url.rstrip("/") + "/mcp"
        self._tokens = token_provider
        self._http = http or httpx.Client(timeout=timeout)
        self._id = 0

    def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._id += 1
        envelope = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }

        resp = self._post(envelope)
        # One retry on 401: token may have been revoked/rotated.
        if resp.status_code == 401:
            invalidate = getattr(self._tokens, "invalidate", None)
            if callable(invalidate):
                invalidate()
            resp = self._post(envelope)
        if resp.status_code == 401:
            raise MemsysAuthError(f"/mcp rejected token: {resp.text[:300]}")
        resp.raise_for_status()

        body = resp.json()
        if "error" in body and body["error"] is not None:
            err = body["error"]
            raise MemsysToolError(err.get("code", 0), err.get("message", ""), err.get("data"))

        # Tool result is a JSON string inside result.content[0].text
        content = body.get("result", {}).get("content", [])
        if content and content[0].get("type") == "text":
            return json.loads(content[0]["text"])
        return body.get("result", {})

    def _post(self, envelope: dict[str, Any]) -> httpx.Response:
        return self._http.post(
            self._url,
            json=envelope,
            headers={
                "Authorization": f"Bearer {self._tokens.get_token()}",
                "Content-Type": "application/json",
            },
        )

    def memory_write(
        self,
        content: str,
        *,
        type: str = "note",
        tags: list[str] | None = None,
        team_id: str | None = None,
        slug_clue: str | None = None,
        metadata: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Write a memory. ``note`` needs no team/slug; ``decision``/``fact``
        require ``team_id`` (the service tenant's default team) + ``slug_clue``.
        Returns the memory result dict (id, version, embedding_status, ...)."""
        args: dict[str, Any] = {"content": content, "type": type}
        if tags:
            args["tags"] = tags
        if team_id:
            args["team_id"] = team_id
        if slug_clue:
            args["slug_clue"] = slug_clue
        if metadata:
            args["metadata"] = metadata
        args.update(extra)
        return self._call("memory_write", args)

    def memory_search(
        self,
        query: str,
        *,
        limit: int = 10,
        tags: list[str] | None = None,
        type: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Hybrid semantic+keyword search. Returns {results: [...], ...}."""
        args: dict[str, Any] = {"query": query, "limit": limit}
        if tags:
            args["tags"] = tags
        if type:
            args["type"] = type
        args.update(extra)
        return self._call("memory_search", args)


# --------------------------------------------------------------------------
# Env-driven factory
# --------------------------------------------------------------------------
def from_env(env: dict[str, str] | None = None) -> MemsysClient:
    """Build a MemsysClient from MEMSYS_* environment variables.

    Common:
      MEMSYS_BASE_URL       http://localhost:8080 | https://memsys.dheemantech.in
      MEMSYS_AUTH_MODE      local_admin | client_credentials

    client_credentials (prod):
      MEMSYS_TOKEN_URL      https://memauth.dheemantech.in/oauth2/token
      MEMSYS_CLIENT_ID      (SSM /mem-mcp/kite/client_id)
      MEMSYS_CLIENT_SECRET  (SSM /mem-mcp/kite/client_secret)
      MEMSYS_SCOPES         "https://memsys.dheemantech.in/memory.read \
                             https://memsys.dheemantech.in/memory.write"

    local_admin (dev):
      MEMSYS_COGNITO_LOCAL_URL  http://localhost:9229
      MEMSYS_POOL_ID            local_memmcp
      MEMSYS_CLIENT_ID          memsys-local-client
      MEMSYS_DEV_USERNAME       admin@local
      MEMSYS_DEV_PASSWORD       Admin@Local2026!
    """
    e = env if env is not None else os.environ
    base_url = e.get("MEMSYS_BASE_URL", "http://localhost:8080")
    mode = e.get("MEMSYS_AUTH_MODE", "local_admin")

    provider: TokenProvider
    if mode == "client_credentials":
        resource = "https://memsys.dheemantech.in"
        provider = ClientCredentialsTokenProvider(
            token_url=e["MEMSYS_TOKEN_URL"],
            client_id=e["MEMSYS_CLIENT_ID"],
            client_secret=e["MEMSYS_CLIENT_SECRET"],
            scopes=e.get(
                "MEMSYS_SCOPES",
                f"{resource}/memory.read {resource}/memory.write",
            ),
        )
    elif mode == "local_admin":
        provider = LocalAdminTokenProvider(
            cognito_local_url=e.get("MEMSYS_COGNITO_LOCAL_URL", "http://localhost:9229"),
            pool_id=e.get("MEMSYS_POOL_ID", "local_memmcp"),
            client_id=e.get("MEMSYS_CLIENT_ID", "memsys-local-client"),
            username=e.get("MEMSYS_DEV_USERNAME", "admin@local"),
            password=e.get("MEMSYS_DEV_PASSWORD", "Admin@Local2026!"),
        )
    else:
        raise MemsysError(f"unknown MEMSYS_AUTH_MODE: {mode!r}")

    return MemsysClient(base_url, provider)


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    """Debug helper: decode (unverified) JWT payload — handy to confirm the
    token's `sub` matches what scripts/seed-service-tenant.sh seeded."""
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


if __name__ == "__main__":
    # Smoke test: write one memory and read it back.
    mem = from_env()
    written = mem.memory_write(
        "kite→memsys smoke test note",
        type="note",
        tags=["kite", "smoke"],
    )
    print("wrote:", written.get("id"), "embedding:", written.get("embedding_status"))
    found = mem.memory_search("kite smoke test", limit=3)
    print("found:", len(found.get("results", [])), "results")
