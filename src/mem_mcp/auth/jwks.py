"""JWKS fetch + cache for Cognito JWT validation.

Per spec §6.8.1: cache 1h TTL; refresh on kid miss (handles Cognito key rotation).

Per GUIDELINES §1.2: HTTP access goes through a Protocol seam so tests can
inject fakes without real httpx calls.

Public API:
    JwksFetcher (Protocol)
    HttpxJwksFetcher  — production impl
    JwksCache         — wraps a fetcher; .get_key(kid), .refresh()
    JwksError         — typed exception with .code
    JwksPayload       — TypedDict alias (whatever Cognito returns)
    JwkKey            — TypedDict alias for one entry in payload['keys']
"""

from __future__ import annotations

import time
from typing import Any, Literal, Protocol, TypedDict


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class JwkKey(TypedDict, total=False):
    """Shape of one entry in Cognito's JWKS payload['keys'] list.

    Marked total=False because Cognito has occasionally added optional fields.
    Required for our use: kid, kty, n, e, alg, use.
    """

    kid: str
    kty: str
    n: str
    e: str
    alg: str
    use: str


class JwksPayload(TypedDict):
    """Top-level JWKS document shape."""

    keys: list[JwkKey]


JwksErrorCode = Literal["unknown_kid", "fetch_failed", "invalid_payload"]


class JwksError(Exception):
    """Raised on JWKS fetch / lookup failures."""

    def __init__(self, code: JwksErrorCode, message: str = "") -> None:
        self.code: JwksErrorCode = code
        super().__init__(message or code)


# ---------------------------------------------------------------------------
# Protocol + production impl
# ---------------------------------------------------------------------------


class JwksFetcher(Protocol):
    """Boundary for fetching the JWKS document.

    Tests inject fakes; production wires HttpxJwksFetcher.
    """

    async def fetch(self) -> JwksPayload: ...


class HttpxJwksFetcher:
    """Production JwksFetcher that GETs the Cognito JWKS URL via httpx."""

    def __init__(self, region: str, user_pool_id: str, timeout_seconds: float = 5.0) -> None:
        self.url = (
            f"https://cognito-idp.{region}.amazonaws.com/"
            f"{user_pool_id}/.well-known/jwks.json"
        )
        self.timeout_seconds = timeout_seconds

    async def fetch(self) -> JwksPayload:
        # Local import keeps unit tests from paying httpx import cost
        import httpx

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(self.url)
                resp.raise_for_status()
                payload: Any = resp.json()
        except httpx.HTTPError as exc:
            raise JwksError("fetch_failed", f"{type(exc).__name__}: {exc}") from exc
        except ValueError as exc:  # JSON decode error
            raise JwksError("invalid_payload", f"non-JSON response: {exc}") from exc

        if not isinstance(payload, dict) or "keys" not in payload or not isinstance(payload["keys"], list):
            raise JwksError("invalid_payload", "missing or non-list 'keys'")
        return payload  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class ClockFn(Protocol):
    """Type alias for the clock injection."""

    def __call__(self) -> float: ...


class JwksCache:
    """In-process JWKS cache with TTL + kid-miss refresh.

    Thread/task-safety: a process-wide singleton would need a lock; in v1
    each uvicorn worker has its own cache and we don't share state. The
    only race is two requests arriving with the same unknown kid — both
    will trigger refresh, which is harmless (last write wins; both end up
    with the same payload).
    """

    def __init__(
        self,
        fetcher: JwksFetcher,
        ttl_seconds: int = 3600,
        clock: ClockFn | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._ttl_seconds = ttl_seconds
        self._clock = clock or time.monotonic
        self._payload: JwksPayload | None = None
        self._fetched_at: float | None = None

    def _stale(self) -> bool:
        if self._payload is None or self._fetched_at is None:
            return True
        return (self._clock() - self._fetched_at) >= self._ttl_seconds

    async def refresh(self) -> None:
        self._payload = await self._fetcher.fetch()
        self._fetched_at = self._clock()

    async def get_key(self, kid: str) -> JwkKey:
        """Return the JWK with matching ``kid``.

        Strategy:
          1. If cache stale → refresh, then look up.
          2. If cache fresh and kid missing → refresh once, then look up.
          3. If still missing after refresh → raise JwksError(unknown_kid).
        """
        if self._stale():
            await self.refresh()

        key = self._find(kid)
        if key is not None:
            return key

        # Kid miss — Cognito rotated keys. Force refresh.
        await self.refresh()
        key = self._find(kid)
        if key is None:
            raise JwksError("unknown_kid", f"no key with kid={kid!r} after refresh")
        return key

    def _find(self, kid: str) -> JwkKey | None:
        if self._payload is None:
            return None
        for k in self._payload["keys"]:
            if k.get("kid") == kid:
                return k
        return None
