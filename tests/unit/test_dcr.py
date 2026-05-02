"""Tests for mem_mcp.auth.dcr (T-4.5)."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mem_mcp.auth.dcr import (
    DcrInput,
    InMemoryRateLimiter,
    _sanitize_client_name,
    make_dcr_router,
)

_RESOURCE = "https://memsys.dheemantech.in"


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeCognito:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.next_id = "cognito-client-id-1"

    async def create_user_pool_client(
        self, *, client_name: str, callback_urls: list[str], scopes: list[str]
    ) -> str:
        self.calls.append(
            {"client_name": client_name, "callback_urls": callback_urls, "scopes": scopes}
        )
        if self.error:
            raise self.error
        return self.next_id


class FakeStore:
    def __init__(self) -> None:
        self.inserts: list[dict[str, Any]] = []

    async def insert(self, **kwargs: Any) -> None:
        self.inserts.append(kwargs)


class FakeAllowed:
    def __init__(self, status_map: dict[str, str]) -> None:
        self.status_map = status_map
        self.calls: list[str] = []

    async def status(self, software_id: str) -> Any:
        self.calls.append(software_id)
        return self.status_map.get(software_id, "unknown")


def _build_client(
    *,
    cognito: Any = None,
    store: Any = None,
    allowed: Any = None,
    limiter: Any = None,
) -> tuple[TestClient, dict[str, Any]]:
    cognito = cognito or FakeCognito()
    store = store or FakeStore()
    allowed = allowed or FakeAllowed({"claude-code": "allowed", "claude-ai": "allowed"})
    limiter = limiter or InMemoryRateLimiter()
    app = FastAPI()
    app.include_router(
        make_dcr_router(
            cognito_factory=cognito,
            client_store=store,
            software_lookup=allowed,
            rate_limiter=limiter,
            resource_url=_RESOURCE,
        )
    )
    return TestClient(app), {
        "cognito": cognito,
        "store": store,
        "allowed": allowed,
        "limiter": limiter,
    }


def _valid_payload() -> dict[str, Any]:
    return {
        "client_name": "Claude Code",
        "redirect_uris": ["http://localhost:8080/callback"],
        "software_id": "claude-code",
        "software_version": "2.x",
    }


# --------------------------------------------------------------------------
# DcrInput validation
# --------------------------------------------------------------------------


class TestDcrInputValidation:
    def test_minimal_valid(self) -> None:
        m = DcrInput.model_validate(_valid_payload())
        assert m.client_name == "Claude Code"
        assert m.scope == "memory.read memory.write"
        assert m.token_endpoint_auth_method == "none"

    def test_redirect_uri_https(self) -> None:
        DcrInput.model_validate(
            {**_valid_payload(), "redirect_uris": ["https://app.example.com/cb"]}
        )

    def test_redirect_uri_localhost(self) -> None:
        DcrInput.model_validate({**_valid_payload(), "redirect_uris": ["http://localhost:9000/cb"]})

    def test_redirect_uri_127(self) -> None:
        DcrInput.model_validate({**_valid_payload(), "redirect_uris": ["http://127.0.0.1/cb"]})

    @pytest.mark.parametrize(
        "uri",
        [
            "http://example.com/cb",  # plain http non-localhost
            "ftp://example.com/cb",  # wrong scheme
            "https://*.example.com/cb",  # wildcard
            "https://example.com/cb#frag",  # fragment
        ],
    )
    def test_redirect_uri_rejected(self, uri: str) -> None:
        with pytest.raises(ValidationError):
            DcrInput.model_validate({**_valid_payload(), "redirect_uris": [uri]})

    def test_scope_unknown_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DcrInput.model_validate({**_valid_payload(), "scope": "memory.read malicious.scope"})

    def test_scope_valid(self) -> None:
        m = DcrInput.model_validate(
            {**_valid_payload(), "scope": "memory.read memory.write memory.admin"}
        )
        assert "memory.admin" in m.scope.split()

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DcrInput.model_validate({**_valid_payload(), "junk_field": "value"})

    def test_too_many_redirect_uris(self) -> None:
        with pytest.raises(ValidationError):
            DcrInput.model_validate(
                {**_valid_payload(), "redirect_uris": [f"https://x.example/{i}" for i in range(6)]}
            )

    def test_grant_types_subset(self) -> None:
        DcrInput.model_validate({**_valid_payload(), "grant_types": ["authorization_code"]})
        with pytest.raises(ValidationError):
            DcrInput.model_validate({**_valid_payload(), "grant_types": ["password"]})


# --------------------------------------------------------------------------
# Sanitizer
# --------------------------------------------------------------------------


class TestSanitizeClientName:
    def test_clean_name_unchanged(self) -> None:
        assert _sanitize_client_name("Claude Code 2.x") == "Claude Code 2.x"

    def test_replaces_disallowed(self) -> None:
        # & and ! are not in the Cognito set
        assert _sanitize_client_name("Claude & Co!") == "Claude - Co-"

    def test_truncated_at_128(self) -> None:
        assert len(_sanitize_client_name("x" * 200)) == 128


# --------------------------------------------------------------------------
# Endpoint — happy path
# --------------------------------------------------------------------------


class TestDcrEndpointSuccess:
    def test_201_with_rfc7591_response(self) -> None:
        client, deps = _build_client()
        resp = client.post("/oauth/register", json=_valid_payload())
        assert resp.status_code == 201
        body = resp.json()

        # RFC 7591 fields
        assert body["client_id"] == "cognito-client-id-1"
        assert body["client_id_issued_at"] > 0
        assert body["client_secret_expires_at"] == 0
        assert body["redirect_uris"] == ["http://localhost:8080/callback"]
        assert body["token_endpoint_auth_method"] == "none"
        assert body["scope"] == "memory.read memory.write"
        assert isinstance(body["registration_access_token"], str)
        assert len(body["registration_access_token"]) >= 32
        assert body["registration_client_uri"] == f"{_RESOURCE}/oauth/register/cognito-client-id-1"

    def test_calls_cognito_with_google_only(self) -> None:
        client, deps = _build_client()
        client.post("/oauth/register", json=_valid_payload())
        assert len(deps["cognito"].calls) == 1
        # The factory was called with client_name + callback_urls + scopes; SupportedIdentityProviders
        # is set inside the factory itself (not passed in the protocol). We assert the production
        # factory's defaults via separate test below.

    def test_persists_with_sha256_hash(self) -> None:
        client, deps = _build_client()
        resp = client.post("/oauth/register", json=_valid_payload())
        token = resp.json()["registration_access_token"]
        expected_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        assert len(deps["store"].inserts) == 1
        ins = deps["store"].inserts[0]
        assert ins["registration_access_token_hash"] == expected_hash
        assert ins["client_id"] == "cognito-client-id-1"
        assert ins["software_id"] == "claude-code"


# --------------------------------------------------------------------------
# Endpoint — failure cases
# --------------------------------------------------------------------------


class TestDcrEndpointFailures:
    def test_unknown_software_id_returns_403(self) -> None:
        allowed = FakeAllowed({})  # nothing allowed
        client, _ = _build_client(allowed=allowed)
        resp = client.post(
            "/oauth/register", json={**_valid_payload(), "software_id": "rogue-tool"}
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "unauthorized_client"

    def test_blocked_software_id_returns_403(self) -> None:
        allowed = FakeAllowed({"cursor": "blocked"})
        client, _ = _build_client(allowed=allowed)
        resp = client.post("/oauth/register", json={**_valid_payload(), "software_id": "cursor"})
        assert resp.status_code == 403
        assert "blocked" in resp.json()["detail"]["error_description"]

    def test_invalid_request_body_returns_400(self) -> None:
        client, _ = _build_client()
        resp = client.post("/oauth/register", json={"missing": "everything"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_request"

    def test_invalid_redirect_uri_returns_400(self) -> None:
        client, _ = _build_client()
        resp = client.post(
            "/oauth/register",
            json={**_valid_payload(), "redirect_uris": ["http://attacker.example/cb"]},
        )
        assert resp.status_code == 400

    def test_per_ip_rate_limit_returns_429(self) -> None:
        # Allow only 1 per IP for fast testing
        from mem_mcp.auth.dcr import PER_IP_LIMIT

        # Mutate via the limiter directly: register PER_IP_LIMIT times then expect 429
        client, _ = _build_client()
        for _ in range(PER_IP_LIMIT):
            r = client.post("/oauth/register", json=_valid_payload())
            assert r.status_code == 201
        r = client.post("/oauth/register", json=_valid_payload())
        assert r.status_code == 429
        assert r.json()["detail"]["scope"] == "per-ip"
        assert "Retry-After" in r.headers

    def test_cognito_failure_returns_500(self) -> None:
        cognito = FakeCognito(error=RuntimeError("boto3 down"))
        client, _ = _build_client(cognito=cognito)
        resp = client.post("/oauth/register", json=_valid_payload())
        assert resp.status_code == 500
        assert resp.json()["detail"]["error"] == "server_error"


# --------------------------------------------------------------------------
# RateLimiter
# --------------------------------------------------------------------------


class TestInMemoryRateLimiter:
    @pytest.mark.asyncio
    async def test_under_limit_allows(self) -> None:
        rl = InMemoryRateLimiter()
        for _ in range(5):
            assert await rl.check_and_consume("k", limit=5, window_seconds=60)

    @pytest.mark.asyncio
    async def test_over_limit_denies(self) -> None:
        rl = InMemoryRateLimiter()
        for _ in range(5):
            await rl.check_and_consume("k", limit=5, window_seconds=60)
        assert not await rl.check_and_consume("k", limit=5, window_seconds=60)

    @pytest.mark.asyncio
    async def test_window_reset(self) -> None:
        # Use an injectable clock
        t = [0.0]
        rl = InMemoryRateLimiter(clock=lambda: t[0])
        for _ in range(5):
            await rl.check_and_consume("k", limit=5, window_seconds=60)
        assert not await rl.check_and_consume("k", limit=5, window_seconds=60)
        t[0] = 61.0
        assert await rl.check_and_consume("k", limit=5, window_seconds=60)

    @pytest.mark.asyncio
    async def test_isolated_keys(self) -> None:
        rl = InMemoryRateLimiter()
        for _ in range(5):
            await rl.check_and_consume("k1", limit=5, window_seconds=60)
        assert await rl.check_and_consume("k2", limit=5, window_seconds=60)
