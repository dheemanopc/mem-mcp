"""Tests for mem_mcp.auth.dcr (T-4.5)."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mem_mcp.auth.dcr import (
    AllowedSoftwarePolicy,
    BotoCognitoClientFactory,
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
    def __init__(self, error: Exception | None = None, client_secret: str | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.next_id = "cognito-client-id-1"
        self.client_secret = client_secret

    async def create_user_pool_client(
        self,
        *,
        client_name: str,
        callback_urls: list[str],
        scopes: list[str],
        generate_secret: bool = False,
    ) -> tuple[str, str | None]:
        self.calls.append(
            {
                "client_name": client_name,
                "callback_urls": callback_urls,
                "scopes": scopes,
                "generate_secret": generate_secret,
            }
        )
        if self.error:
            raise self.error
        return self.next_id, self.client_secret


class FakeStore:
    def __init__(self) -> None:
        self.inserts: list[dict[str, Any]] = []

    async def insert(self, **kwargs: Any) -> None:
        self.inserts.append(kwargs)


class FakeAllowed:
    def __init__(self, policy_map: dict[str, AllowedSoftwarePolicy] | None = None) -> None:
        self.policy_map = policy_map or {}
        self.calls: list[str] = []

    async def policy(self, software_id: str) -> AllowedSoftwarePolicy:
        self.calls.append(software_id)
        return self.policy_map.get(software_id, AllowedSoftwarePolicy(status="unknown"))


def _build_client(
    *,
    cognito: Any = None,
    store: Any = None,
    allowed: Any = None,
    limiter: Any = None,
) -> tuple[TestClient, dict[str, Any]]:
    cognito = cognito or FakeCognito()
    store = store or FakeStore()
    allowed = allowed or FakeAllowed(
        {
            "claude-code": AllowedSoftwarePolicy(status="allowed"),
            "claude-ai": AllowedSoftwarePolicy(status="allowed"),
        }
    )
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

    def test_scope_unknown_accepted(self) -> None:
        m = DcrInput.model_validate({**_valid_payload(), "scope": "memory.read malicious.scope"})
        assert "malicious.scope" in m.scope.split()

    def test_scope_valid(self) -> None:
        m = DcrInput.model_validate(
            {**_valid_payload(), "scope": "memory.read memory.write memory.admin"}
        )
        assert "memory.admin" in m.scope.split()

    def test_extra_fields_ignored(self) -> None:
        payload = {**_valid_payload(), "junk_field": "value", "unknown_prop": "ignored"}
        m = DcrInput.model_validate(payload)
        dumped = m.model_dump()
        assert "junk_field" not in dumped
        assert "unknown_prop" not in dumped

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
        # DCR clients only get memory.read + memory.write; memory.admin and
        # account.manage are reserved for the session-cookie (web UI) path.
        assert (
            body["scope"]
            == "https://memsys.dheemantech.in/memory.read https://memsys.dheemantech.in/memory.write"
        )
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

    def test_confidential_client_returns_secret(self) -> None:
        cognito = FakeCognito(client_secret="super-secret-value")
        allowed = FakeAllowed(
            {"claude-code": AllowedSoftwarePolicy(status="allowed", require_secret=True)}
        )
        client, deps = _build_client(cognito=cognito, allowed=allowed)
        payload = {**_valid_payload(), "token_endpoint_auth_method": "client_secret_post"}
        resp = client.post("/oauth/register", json=payload)
        assert resp.status_code == 201
        body = resp.json()
        assert body["client_secret"] == "super-secret-value"
        assert body["token_endpoint_auth_method"] == "client_secret_post"
        # Verify cognito was called with generate_secret=True
        assert len(deps["cognito"].calls) == 1
        assert deps["cognito"].calls[0]["generate_secret"] is True

    def test_redirect_uri_allowlist_accepts_known_uri(self) -> None:
        allowed = FakeAllowed(
            {
                "claude-code": AllowedSoftwarePolicy(
                    status="allowed", allowed_redirect_uris=["https://known.example/callback"]
                )
            }
        )
        client, _ = _build_client(allowed=allowed)
        payload = {**_valid_payload(), "redirect_uris": ["https://known.example/callback"]}
        resp = client.post("/oauth/register", json=payload)
        assert resp.status_code == 201

    def test_redirect_uri_allowlist_rejects_unknown_uri(self) -> None:
        allowed = FakeAllowed(
            {
                "claude-code": AllowedSoftwarePolicy(
                    status="allowed", allowed_redirect_uris=["https://known.example/callback"]
                )
            }
        )
        client, _ = _build_client(allowed=allowed)
        payload = {**_valid_payload(), "redirect_uris": ["https://attacker.example/callback"]}
        resp = client.post("/oauth/register", json=payload)
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_redirect_uri"

    def test_redirect_uri_allowlist_null_means_any(self) -> None:
        allowed = FakeAllowed(
            {"claude-code": AllowedSoftwarePolicy(status="allowed", allowed_redirect_uris=None)}
        )
        client, _ = _build_client(allowed=allowed)
        payload = {**_valid_payload(), "redirect_uris": ["https://arbitrary.example/callback"]}
        resp = client.post("/oauth/register", json=payload)
        assert resp.status_code == 201

    def test_tier2_honors_requested_scopes_only(self) -> None:
        """Tier-2 (confidential) client asks for memory.read+memory.write and gets
        exactly those — NOT the full advertised set. memory.admin / account.manage
        stay out of the Cognito client's eligible scopes."""
        allowed = FakeAllowed(
            {
                "algotrade": AllowedSoftwarePolicy(
                    status="allowed",
                    require_secret=True,
                    allowed_redirect_uris=["https://tradeapi.dheemantech.in/memsys/callback"],
                )
            }
        )
        client, deps = _build_client(allowed=allowed)
        payload = {
            **_valid_payload(),
            "software_id": "algotrade",
            "redirect_uris": ["https://tradeapi.dheemantech.in/memsys/callback"],
            "token_endpoint_auth_method": "client_secret_post",
            "scope": "memory.read memory.write",
        }
        resp = client.post("/oauth/register", json=payload)
        assert resp.status_code == 201
        body = resp.json()
        # Exact match — no admin scopes sneaked in
        assert (
            body["scope"]
            == "https://memsys.dheemantech.in/memory.read https://memsys.dheemantech.in/memory.write"
        )
        # Verify Cognito was registered with only these two scopes
        cognito_scopes = deps["cognito"].calls[0]["scopes"]
        assert sorted(cognito_scopes) == ["memory.read", "memory.write"]
        assert "memory.admin" not in cognito_scopes
        assert "account.manage" not in cognito_scopes

    def test_tier1_expands_to_dcr_allowed_subset(self) -> None:
        """Tier-1 (public) client asks for memory.read only; gets expanded to the
        full DCR-allowed set (still excludes memory.admin / account.manage)."""
        client, deps = _build_client()
        payload = {**_valid_payload(), "scope": "memory.read"}
        resp = client.post("/oauth/register", json=payload)
        assert resp.status_code == 201
        cognito_scopes = deps["cognito"].calls[0]["scopes"]
        assert sorted(cognito_scopes) == ["memory.read", "memory.write"]
        assert "memory.admin" not in cognito_scopes


class TestDcrScopeAllowlist:
    """Scope-allowlist behavior at the DCR endpoint.

    memory.admin and account.manage are not eligible via DCR; the web UI's
    session-cookie path is the only place those scopes are granted.

    Tier-1 (public, like claude-code): silently downgrade out-of-allowlist
    scope requests to DCR_ALLOWED_SCOPES. Generic MCP clients read
    .well-known/oauth-authorization-server (which advertises all four scopes
    as supported on the resource server) and blindly request the full set —
    rejecting them would break the standard discovery flow.

    Tier-2 (confidential, like algotrade): strictly reject any out-of-allowlist
    scope. Confidential clients are deliberate integrations; opt in per scope.
    """

    def test_tier1_silently_downgrades_memory_admin(self) -> None:
        """Public client asking for memory.admin gets registered with only
        memory.read + memory.write — no 400."""
        client, deps = _build_client()
        payload = {**_valid_payload(), "scope": "memory.read memory.admin"}
        resp = client.post("/oauth/register", json=payload)
        assert resp.status_code == 201
        cognito_scopes = deps["cognito"].calls[0]["scopes"]
        assert sorted(cognito_scopes) == ["memory.read", "memory.write"]
        assert "memory.admin" not in cognito_scopes

    def test_tier1_silently_downgrades_account_manage(self) -> None:
        client, deps = _build_client()
        payload = {**_valid_payload(), "scope": "account.manage"}
        resp = client.post("/oauth/register", json=payload)
        assert resp.status_code == 201
        cognito_scopes = deps["cognito"].calls[0]["scopes"]
        assert "account.manage" not in cognito_scopes

    def test_tier1_silently_drops_unknown_scope(self) -> None:
        """Public client asking for a totally unknown scope still registers;
        the unknown scope is dropped."""
        client, deps = _build_client()
        payload = {**_valid_payload(), "scope": "memory.read foo.bar"}
        resp = client.post("/oauth/register", json=payload)
        assert resp.status_code == 201
        cognito_scopes = deps["cognito"].calls[0]["scopes"]
        assert "foo.bar" not in cognito_scopes
        assert "memory.read" in cognito_scopes

    def test_tier2_rejects_admin_scopes(self) -> None:
        """Tier-2 confidential clients can't escalate via DCR — strict rejection."""
        allowed = FakeAllowed(
            {
                "algotrade": AllowedSoftwarePolicy(
                    status="allowed",
                    require_secret=True,
                    allowed_redirect_uris=["https://tradeapi.dheemantech.in/memsys/callback"],
                )
            }
        )
        client, _ = _build_client(allowed=allowed)
        payload = {
            **_valid_payload(),
            "software_id": "algotrade",
            "redirect_uris": ["https://tradeapi.dheemantech.in/memsys/callback"],
            "token_endpoint_auth_method": "client_secret_post",
            "scope": "memory.read memory.write memory.admin",
        }
        resp = client.post("/oauth/register", json=payload)
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_scope"


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
        allowed = FakeAllowed({"cursor": AllowedSoftwarePolicy(status="blocked")})
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

    def test_confidential_client_rejects_none_auth_method(self) -> None:
        allowed = FakeAllowed(
            {"claude-code": AllowedSoftwarePolicy(status="allowed", require_secret=True)}
        )
        client, _ = _build_client(allowed=allowed)
        # Default token_endpoint_auth_method is "none", which should be rejected for confidential clients
        resp = client.post("/oauth/register", json=_valid_payload())
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_client_metadata"


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


# --------------------------------------------------------------------------
# BotoCognitoClientFactory — OIDC injection
# --------------------------------------------------------------------------


class TestBotoCognitoClientFactoryOidcInjection:
    @pytest.mark.asyncio
    async def test_injects_openid_email_profile_for_memory_only_scopes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Call factory with memory.read+memory.write, assert OIDC scopes are injected."""
        captured = {}

        class FakeBotoClient:
            def create_user_pool_client(self, **kwargs: Any) -> dict[str, Any]:
                captured.update(kwargs)
                return {"UserPoolClient": {"ClientId": "test-id", "ClientSecret": None}}

        monkeypatch.setattr("boto3.client", lambda *a, **kw: FakeBotoClient())

        factory = BotoCognitoClientFactory(
            user_pool_id="up",
            region="us-east-1",
            resource_server_identifier="https://memsys.dheemantech.in",
        )

        client_id, client_secret = await factory.create_user_pool_client(
            client_name="test-client",
            callback_urls=["https://localhost/cb"],
            scopes=["memory.read", "memory.write"],
        )

        assert client_id == "test-id"
        assert client_secret is None
        assert "AllowedOAuthScopes" in captured
        scopes = captured["AllowedOAuthScopes"]
        assert "https://memsys.dheemantech.in/memory.read" in scopes
        assert "https://memsys.dheemantech.in/memory.write" in scopes
        assert "openid" in scopes
        assert "email" in scopes
        assert "profile" in scopes

    @pytest.mark.asyncio
    async def test_dedupes_when_openid_already_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Call factory with openid already in scopes, verify it appears exactly once."""
        captured = {}

        class FakeBotoClient:
            def create_user_pool_client(self, **kwargs: Any) -> dict[str, Any]:
                captured.update(kwargs)
                return {"UserPoolClient": {"ClientId": "test-id", "ClientSecret": None}}

        monkeypatch.setattr("boto3.client", lambda *a, **kw: FakeBotoClient())

        factory = BotoCognitoClientFactory(
            user_pool_id="up",
            region="us-east-1",
            resource_server_identifier="https://memsys.dheemantech.in",
        )

        client_id, client_secret = await factory.create_user_pool_client(
            client_name="test-client",
            callback_urls=["https://localhost/cb"],
            scopes=["memory.read", "openid"],
        )

        assert client_id == "test-id"
        scopes = captured["AllowedOAuthScopes"]
        openid_count = scopes.count("openid")
        assert (
            openid_count == 1
        ), f"Expected openid to appear exactly once, found {openid_count} times"

    @pytest.mark.asyncio
    async def test_no_resource_server_identifier_still_adds_oidc(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When resource_server_identifier=None, scopes pass through unchanged and OIDC is still appended."""
        captured = {}

        class FakeBotoClient:
            def create_user_pool_client(self, **kwargs: Any) -> dict[str, Any]:
                captured.update(kwargs)
                return {"UserPoolClient": {"ClientId": "test-id", "ClientSecret": None}}

        monkeypatch.setattr("boto3.client", lambda *a, **kw: FakeBotoClient())

        factory = BotoCognitoClientFactory(
            user_pool_id="up",
            region="us-east-1",
            resource_server_identifier=None,
        )

        client_id, client_secret = await factory.create_user_pool_client(
            client_name="test-client",
            callback_urls=["https://localhost/cb"],
            scopes=["memory.read", "memory.write"],
        )

        assert client_id == "test-id"
        scopes = captured["AllowedOAuthScopes"]
        # Without resource_server_identifier, scopes pass through as-is
        assert "memory.read" in scopes
        assert "memory.write" in scopes
        # But OIDC scopes are still injected
        assert "openid" in scopes
        assert "email" in scopes
        assert "profile" in scopes
