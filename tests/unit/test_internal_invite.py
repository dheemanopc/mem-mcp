"""Tests for mem_mcp.auth.internal_invite (T-4.7)."""

from __future__ import annotations

import hmac
from hashlib import sha256
from typing import Any, Literal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mem_mcp.auth.internal_invite import (
    _compute_hmac,
    _verify_hmac,
    make_internal_invite_router,
)


_SECRET = "test-shared-secret-32-bytes-of-randomness-or-so"


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeStore:
    def __init__(
        self,
        states: dict[str, Literal["invited", "not_invited", "already_consumed"]] | None = None,
    ) -> None:
        self.states = states or {}
        self.calls: list[str] = []

    async def lookup(
        self, email: str
    ) -> Literal["invited", "not_invited", "already_consumed"]:
        self.calls.append(email)
        return self.states.get(email.lower(), "not_invited")


def _build(store: Any | None = None, secret: str = _SECRET) -> tuple[TestClient, FakeStore]:
    store = store or FakeStore()
    app = FastAPI()
    app.include_router(make_internal_invite_router(store=store, shared_secret=secret))
    return TestClient(app), store


def _signed_post(
    client: TestClient,
    payload: dict[str, Any],
    *,
    secret: str = _SECRET,
    tamper: bool = False,
    content_type: str = "application/json",
) -> Any:
    import json as _json

    body = _json.dumps(payload).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()
    if tamper:
        sig = "0" * len(sig)
    return client.post(
        "/internal/check_invite",
        content=body,
        headers={"X-Internal-Auth": sig, "Content-Type": content_type},
    )


# --------------------------------------------------------------------------
# HMAC helpers
# --------------------------------------------------------------------------


class TestHelpers:
    def test_compute_hmac_deterministic(self) -> None:
        h1 = _compute_hmac(_SECRET, b"hello")
        h2 = _compute_hmac(_SECRET, b"hello")
        assert h1 == h2

    def test_compute_hmac_different_for_different_body(self) -> None:
        assert _compute_hmac(_SECRET, b"a") != _compute_hmac(_SECRET, b"b")

    def test_verify_hmac_matches(self) -> None:
        h = _compute_hmac(_SECRET, b"x")
        assert _verify_hmac(h, h) is True

    def test_verify_hmac_mismatch(self) -> None:
        assert _verify_hmac("0" * 64, "1" * 64) is False


# --------------------------------------------------------------------------
# Auth failures
# --------------------------------------------------------------------------


class TestAuthFailures:
    def test_missing_header_returns_401(self) -> None:
        client, _ = _build()
        resp = client.post(
            "/internal/check_invite",
            json={"email": "anand@dheemantech.com"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["reason"] == "missing_internal_auth"

    def test_wrong_signature_returns_401(self) -> None:
        client, _ = _build()
        resp = _signed_post(client, {"email": "anand@dheemantech.com"}, tamper=True)
        assert resp.status_code == 401
        assert resp.json()["detail"]["reason"] == "hmac_mismatch"

    def test_signature_with_wrong_secret_returns_401(self) -> None:
        client, _ = _build(secret="server-secret")
        # Sign with a different secret
        resp = _signed_post(client, {"email": "x@y.com"}, secret="lambda-thinks-this")
        assert resp.status_code == 401


# --------------------------------------------------------------------------
# Body validation
# --------------------------------------------------------------------------


class TestBodyValidation:
    def test_missing_email_returns_400(self) -> None:
        client, _ = _build()
        resp = _signed_post(client, {})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_request"

    def test_malformed_email_returns_400(self) -> None:
        client, _ = _build()
        resp = _signed_post(client, {"email": "not-an-email"})
        assert resp.status_code == 400

    def test_extra_fields_rejected(self) -> None:
        client, _ = _build()
        resp = _signed_post(client, {"email": "x@y.com", "junk": "value"})
        assert resp.status_code == 400


# --------------------------------------------------------------------------
# Decision logic
# --------------------------------------------------------------------------


class TestDecisions:
    def test_invited_email_returns_allow(self) -> None:
        store = FakeStore({"anand@dheemantech.com": "invited"})
        client, _ = _build(store)
        resp = _signed_post(client, {"email": "anand@dheemantech.com"})
        assert resp.status_code == 200
        assert resp.json() == {"decision": "allow", "reason": "invited"}

    def test_not_invited_returns_deny(self) -> None:
        client, _ = _build(FakeStore({}))
        resp = _signed_post(client, {"email": "stranger@example.com"})
        assert resp.status_code == 200
        assert resp.json() == {"decision": "deny", "reason": "not_invited"}

    def test_already_consumed_returns_deny(self) -> None:
        store = FakeStore({"already@used.com": "already_consumed"})
        client, _ = _build(store)
        resp = _signed_post(client, {"email": "already@used.com"})
        assert resp.status_code == 200
        assert resp.json() == {"decision": "deny", "reason": "already_consumed"}

    def test_email_lookup_is_case_insensitive(self) -> None:
        store = FakeStore({"anand@dheemantech.com": "invited"})
        client, _ = _build(store)
        resp = _signed_post(client, {"email": "Anand@DheemanTech.COM"})
        assert resp.status_code == 200
        assert resp.json()["decision"] == "allow"


# --------------------------------------------------------------------------
# Provider context (optional field)
# --------------------------------------------------------------------------


class TestProviderField:
    def test_provider_optional(self) -> None:
        store = FakeStore({"x@y.com": "invited"})
        client, _ = _build(store)
        resp = _signed_post(client, {"email": "x@y.com", "provider": "google"})
        assert resp.status_code == 200
