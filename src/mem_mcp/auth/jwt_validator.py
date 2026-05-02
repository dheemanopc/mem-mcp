"""Cognito JWT validation.

Validates access tokens against Cognito JWKS per FR-6.8.1..6.8.5.

Tokens are verified for:
  - signature (RS256, key looked up via JwksCache by 'kid' header)
  - iss matches configured Cognito issuer
  - token_use == 'access'
  - exp > now (with 0s skew on expiry — strict)
  - iat <= now + 60s (small clock-skew tolerance)
  - required claims present

The 'client_id' claim is exposed in JwtClaims but NOT cross-checked against
oauth_clients here — that lookup happens in the Bearer middleware (T-4.3),
which has DB access.

Public API:
    JwtClaims     — frozen dataclass of validated claims
    JwtError      — exception with typed .code
    JwtValidator  — class wrapping a JwksCache + issuer config
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from mem_mcp.auth.jwks import JwksCache, JwksError

JwtErrorCode = Literal[
    "malformed",
    "bad_signature",
    "wrong_iss",
    "wrong_aud",
    "expired",
    "missing_claim",
]


class JwtError(Exception):
    """Raised when JWT validation fails. Carries a machine-readable .code."""

    def __init__(self, code: JwtErrorCode, message: str = "") -> None:
        self.code: JwtErrorCode = code
        super().__init__(message or code)


@dataclass(frozen=True)
class JwtClaims:
    """Validated subset of Cognito access-token claims."""

    sub: str
    iss: str
    client_id: str
    token_use: str
    scopes: tuple[str, ...]
    exp: int
    iat: int
    nbf: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


# Type alias for clock injection (test seam)
class ClockFn:
    """Callable returning UNIX epoch seconds. Default: time.time."""

    def __call__(self) -> float:  # type: ignore[empty-body]
        ...


class JwtValidator:
    """Validates Cognito access tokens against a JwksCache + issuer config."""

    def __init__(
        self,
        jwks_cache: JwksCache,
        issuer: str,
        clock: ClockFn | None = None,
        clock_skew_seconds: int = 60,
    ) -> None:
        self._jwks = jwks_cache
        self._issuer = issuer
        self._clock = clock or time.time  # type: ignore[assignment]
        self._clock_skew = clock_skew_seconds

    async def validate(self, token: str) -> JwtClaims:
        """Verify the token end-to-end. Raises JwtError on any failure."""
        # Local imports — keeps test paths fast for tests that don't touch crypto
        from jose import jwk, jwt
        from jose.exceptions import JWTError
        from jose.utils import base64url_decode

        # 1) Parse header (without verification) to get kid + alg
        try:
            unverified_header = jwt.get_unverified_header(token)
        except JWTError as exc:
            raise JwtError("malformed", f"unparseable header: {exc}") from exc

        kid = unverified_header.get("kid")
        alg = unverified_header.get("alg")
        if not kid:
            raise JwtError("malformed", "missing 'kid' in header")
        if alg != "RS256":
            raise JwtError(
                "malformed", f"unsupported alg: {alg!r}; only RS256 accepted"
            )

        # 2) Fetch the public key
        try:
            jwk_dict = await self._jwks.get_key(kid)
        except JwksError as exc:
            raise JwtError(
                "bad_signature", f"jwks lookup failed: {exc.code}"
            ) from exc

        # 3) Verify signature manually (python-jose's jwt.decode requires options
        #    we want full control over — verify each piece explicitly)
        try:
            message, encoded_signature = token.rsplit(".", 1)
            decoded_signature = base64url_decode(encoded_signature.encode("utf-8"))
        except ValueError as exc:
            raise JwtError("malformed", f"unparseable token: {exc}") from exc

        try:
            public_key = jwk.construct(jwk_dict, algorithm="RS256")
        except (JWTError, ValueError, KeyError) as exc:
            raise JwtError(
                "bad_signature", f"jwk construct failed: {exc}"
            ) from exc

        if not public_key.verify(message.encode("utf-8"), decoded_signature):
            raise JwtError("bad_signature", "signature does not match")

        # 4) Decode claims (now safe, signature verified)
        try:
            claims: dict[str, Any] = jwt.get_unverified_claims(token)
        except JWTError as exc:
            raise JwtError("malformed", f"unparseable claims: {exc}") from exc

        # 5) Check required claims present
        for required in ("sub", "iss", "client_id", "token_use", "exp", "iat"):
            if required not in claims:
                raise JwtError(
                    "missing_claim", f"missing required claim: {required!r}"
                )

        # 6) iss
        if claims["iss"] != self._issuer:
            raise JwtError(
                "wrong_iss",
                f"iss={claims['iss']!r} != {self._issuer!r}",
            )

        # 7) token_use
        if claims["token_use"] != "access":
            raise JwtError(
                "wrong_aud",
                f"token_use={claims['token_use']!r}, expected 'access'",
            )

        # 8) Time validation
        now = float(self._clock())  # type: ignore[operator]
        exp = int(claims["exp"])
        iat = int(claims["iat"])

        if exp <= now:
            raise JwtError("expired", f"exp={exp} <= now={now:.0f}")

        nbf_raw = claims.get("nbf")
        nbf: int | None = None
        if nbf_raw is not None:
            nbf = int(nbf_raw)
            if nbf > now:
                raise JwtError("expired", f"nbf={nbf} > now={now:.0f}")

        if iat > now + self._clock_skew:
            raise JwtError(
                "malformed",
                f"iat={iat} > now+skew={now + self._clock_skew:.0f} (clock skew?)",
            )

        # 9) Build claims object
        scope_str = claims.get("scope") or ""
        scopes = tuple(s for s in scope_str.split() if s)

        return JwtClaims(
            sub=str(claims["sub"]),
            iss=str(claims["iss"]),
            client_id=str(claims["client_id"]),
            token_use=str(claims["token_use"]),
            scopes=scopes,
            exp=exp,
            iat=iat,
            nbf=nbf,
            raw=claims,
        )
