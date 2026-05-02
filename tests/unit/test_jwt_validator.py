"""Tests for mem_mcp.auth.jwt_validator (T-4.2)."""

from __future__ import annotations

import time
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from mem_mcp.auth.jwks import JwksError
from mem_mcp.auth.jwt_validator import JwtClaims, JwtError, JwtValidator

_ISSUER = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_TESTPOOL"


# ---------------------------------------------------------------------------
# RSA keypair + JWK helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[Any, Any]:
    """One RSA keypair for all tests in this module (generation is slow)."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key()
    return private, public


@pytest.fixture
def jwk_dict(rsa_keypair: tuple[Any, Any]) -> dict[str, str]:
    """Convert the public RSA key to a JWK dict suitable for python-jose."""
    from jose import jwk

    _, public = rsa_keypair
    from cryptography.hazmat.primitives import serialization

    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key = jwk.construct(pem.decode("utf-8"), algorithm="RS256")
    raw = key.to_dict()
    raw["kid"] = "test-kid-1"
    raw["use"] = "sig"
    raw["alg"] = "RS256"
    return raw


def _mint_token(
    rsa_keypair: tuple[Any, Any],
    *,
    kid: str = "test-kid-1",
    overrides: dict[str, Any] | None = None,
    issuer: str = _ISSUER,
) -> str:
    """Mint a test JWT with sensible Cognito-shaped defaults."""
    from cryptography.hazmat.primitives import serialization
    from jose import jwt

    private, _ = rsa_keypair
    now = int(time.time())
    claims = {
        "sub": "11111111-2222-3333-4444-555555555555",
        "iss": issuer,
        "client_id": "test-client-id",
        "token_use": "access",
        "scope": "memory.read memory.write",
        "exp": now + 3600,
        "iat": now,
    }
    if overrides:
        claims.update(overrides)

    pem_priv = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(claims, pem_priv.decode("utf-8"), algorithm="RS256", headers={"kid": kid})


# ---------------------------------------------------------------------------
# Fake JwksCache
# ---------------------------------------------------------------------------


class FakeJwksCache:
    """Returns a single canned JWK dict by kid; mimics JwksCache.get_key."""

    def __init__(self, keys: dict[str, dict[str, Any]]) -> None:
        self._keys = keys
        self.calls: list[str] = []

    async def get_key(self, kid: str) -> dict[str, Any]:
        self.calls.append(kid)
        if kid not in self._keys:
            raise JwksError("unknown_kid", f"no key {kid!r}")
        return self._keys[kid]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestValidateHappyPath:
    @pytest.mark.asyncio
    async def test_valid_token_returns_claims(
        self, rsa_keypair: tuple[Any, Any], jwk_dict: dict[str, str]
    ) -> None:
        token = _mint_token(rsa_keypair)
        cache = FakeJwksCache({"test-kid-1": jwk_dict})
        validator = JwtValidator(cache, issuer=_ISSUER)  # type: ignore[arg-type]

        claims = await validator.validate(token)
        assert isinstance(claims, JwtClaims)
        assert claims.sub == "11111111-2222-3333-4444-555555555555"
        assert claims.iss == _ISSUER
        assert claims.client_id == "test-client-id"
        assert claims.token_use == "access"
        assert claims.scopes == ("memory.read", "memory.write")
        assert claims.nbf is None


# ---------------------------------------------------------------------------
# Failure cases — one test per JwtErrorCode
# ---------------------------------------------------------------------------


class TestValidateFailures:
    @pytest.mark.asyncio
    async def test_malformed_token(
        self, rsa_keypair: tuple[Any, Any], jwk_dict: dict[str, str]
    ) -> None:
        cache = FakeJwksCache({"test-kid-1": jwk_dict})
        validator = JwtValidator(cache, issuer=_ISSUER)  # type: ignore[arg-type]
        with pytest.raises(JwtError) as exc_info:
            await validator.validate("not-a-jwt")
        assert exc_info.value.code == "malformed"

    @pytest.mark.asyncio
    async def test_missing_kid(
        self, rsa_keypair: tuple[Any, Any], jwk_dict: dict[str, str]
    ) -> None:
        from cryptography.hazmat.primitives import serialization
        from jose import jwt

        private, _ = rsa_keypair
        pem_priv = private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        token = jwt.encode({"sub": "x"}, pem_priv.decode("utf-8"), algorithm="RS256")
        cache = FakeJwksCache({"test-kid-1": jwk_dict})
        validator = JwtValidator(cache, issuer=_ISSUER)  # type: ignore[arg-type]
        with pytest.raises(JwtError) as exc_info:
            await validator.validate(token)
        assert exc_info.value.code == "malformed"
        assert "kid" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_unknown_kid_yields_bad_signature(
        self, rsa_keypair: tuple[Any, Any], jwk_dict: dict[str, str]
    ) -> None:
        # Mint with a kid the cache doesn't know
        token = _mint_token(rsa_keypair, kid="unknown-kid")
        cache = FakeJwksCache({"test-kid-1": jwk_dict})
        validator = JwtValidator(cache, issuer=_ISSUER)  # type: ignore[arg-type]
        with pytest.raises(JwtError) as exc_info:
            await validator.validate(token)
        assert exc_info.value.code == "bad_signature"

    @pytest.mark.asyncio
    async def test_tampered_signature(
        self, rsa_keypair: tuple[Any, Any], jwk_dict: dict[str, str]
    ) -> None:
        token = _mint_token(rsa_keypair)
        # Flip one character in the signature
        head, body, sig = token.rsplit(".", 2)
        tampered = head + "." + body + "." + sig[:-1] + ("A" if sig[-1] != "A" else "B")
        cache = FakeJwksCache({"test-kid-1": jwk_dict})
        validator = JwtValidator(cache, issuer=_ISSUER)  # type: ignore[arg-type]
        with pytest.raises(JwtError) as exc_info:
            await validator.validate(tampered)
        assert exc_info.value.code == "bad_signature"

    @pytest.mark.asyncio
    async def test_wrong_iss(self, rsa_keypair: tuple[Any, Any], jwk_dict: dict[str, str]) -> None:
        token = _mint_token(rsa_keypair, overrides={"iss": "https://attacker.example/"})
        cache = FakeJwksCache({"test-kid-1": jwk_dict})
        validator = JwtValidator(cache, issuer=_ISSUER)  # type: ignore[arg-type]
        with pytest.raises(JwtError) as exc_info:
            await validator.validate(token)
        assert exc_info.value.code == "wrong_iss"

    @pytest.mark.asyncio
    async def test_id_token_rejected(
        self, rsa_keypair: tuple[Any, Any], jwk_dict: dict[str, str]
    ) -> None:
        token = _mint_token(rsa_keypair, overrides={"token_use": "id"})
        cache = FakeJwksCache({"test-kid-1": jwk_dict})
        validator = JwtValidator(cache, issuer=_ISSUER)  # type: ignore[arg-type]
        with pytest.raises(JwtError) as exc_info:
            await validator.validate(token)
        assert exc_info.value.code == "wrong_aud"

    @pytest.mark.asyncio
    async def test_expired(self, rsa_keypair: tuple[Any, Any], jwk_dict: dict[str, str]) -> None:
        now = int(time.time())
        token = _mint_token(rsa_keypair, overrides={"exp": now - 1, "iat": now - 3600})
        cache = FakeJwksCache({"test-kid-1": jwk_dict})
        validator = JwtValidator(cache, issuer=_ISSUER)  # type: ignore[arg-type]
        with pytest.raises(JwtError) as exc_info:
            await validator.validate(token)
        assert exc_info.value.code == "expired"

    @pytest.mark.asyncio
    async def test_nbf_in_future_rejected(
        self, rsa_keypair: tuple[Any, Any], jwk_dict: dict[str, str]
    ) -> None:
        now = int(time.time())
        token = _mint_token(rsa_keypair, overrides={"nbf": now + 600})
        cache = FakeJwksCache({"test-kid-1": jwk_dict})
        validator = JwtValidator(cache, issuer=_ISSUER)  # type: ignore[arg-type]
        with pytest.raises(JwtError) as exc_info:
            await validator.validate(token)
        assert exc_info.value.code == "expired"

    @pytest.mark.asyncio
    async def test_missing_required_claim(
        self, rsa_keypair: tuple[Any, Any], jwk_dict: dict[str, str]
    ) -> None:
        from cryptography.hazmat.primitives import serialization
        from jose import jwt

        private, _ = rsa_keypair
        now = int(time.time())
        bad_claims = {
            "sub": "x",
            "iss": _ISSUER,
            "token_use": "access",
            "exp": now + 3600,
            "iat": now,
            # no client_id
        }
        pem_priv = private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        token = jwt.encode(
            bad_claims, pem_priv.decode("utf-8"), algorithm="RS256", headers={"kid": "test-kid-1"}
        )
        cache = FakeJwksCache({"test-kid-1": jwk_dict})
        validator = JwtValidator(cache, issuer=_ISSUER)  # type: ignore[arg-type]
        with pytest.raises(JwtError) as exc_info:
            await validator.validate(token)
        assert exc_info.value.code == "missing_claim"
        assert "client_id" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Clock injection
# ---------------------------------------------------------------------------


class TestClockInjection:
    @pytest.mark.asyncio
    async def test_clock_skew_tolerance(
        self, rsa_keypair: tuple[Any, Any], jwk_dict: dict[str, str]
    ) -> None:
        # Mint a token with iat 30s in the future from our fake clock
        now = 1_700_000_000
        token = _mint_token(rsa_keypair, overrides={"iat": now + 30, "exp": now + 3600})
        cache = FakeJwksCache({"test-kid-1": jwk_dict})
        validator = JwtValidator(
            cache,  # type: ignore[arg-type]
            issuer=_ISSUER,
            clock=lambda: float(now),  # type: ignore[arg-type]
            clock_skew_seconds=60,
        )
        # Should pass — 30s is within the 60s skew tolerance
        claims = await validator.validate(token)
        assert claims.sub == "11111111-2222-3333-4444-555555555555"

    @pytest.mark.asyncio
    async def test_clock_skew_exceeded_rejected(
        self, rsa_keypair: tuple[Any, Any], jwk_dict: dict[str, str]
    ) -> None:
        now = 1_700_000_000
        token = _mint_token(rsa_keypair, overrides={"iat": now + 120, "exp": now + 3600})
        cache = FakeJwksCache({"test-kid-1": jwk_dict})
        validator = JwtValidator(
            cache,  # type: ignore[arg-type]
            issuer=_ISSUER,
            clock=lambda: float(now),  # type: ignore[arg-type]
            clock_skew_seconds=60,
        )
        with pytest.raises(JwtError) as exc_info:
            await validator.validate(token)
        assert exc_info.value.code == "malformed"
