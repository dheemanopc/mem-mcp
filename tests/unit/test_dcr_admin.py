"""Tests for mem_mcp.auth.dcr_admin (T-4.6)."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mem_mcp.auth.dcr_admin import (
    _hash_token,
    _verify_token,
    make_dcr_admin_router,
)


_RESOURCE = "https://memsys.dheemantech.in"
_CLIENT_ID = "cognito-client-1"
_TOKEN = "secret-registration-token-abcdef"
_TOKEN_HASH = hashlib.sha256(_TOKEN.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeLookup:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.calls: list[str] = []

    async def fetch(self, client_id: str) -> dict[str, Any] | None:
        self.calls.append(client_id)
        if self.row is None:
            return None
        if self.row["id"] != client_id:
            return None
        return self.row


class FakeDbDeleter:
    def __init__(self, returns: bool = True) -> None:
        self.returns = returns
        self.calls: list[str] = []

    async def delete(self, client_id: str) -> bool:
        self.calls.append(client_id)
        return self.returns


class FakeCognitoDeleter:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[str] = []

    async def delete_user_pool_client(self, client_id: str) -> None:
        self.calls.append(client_id)
        if self.error is not None:
            raise self.error


def _row(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": _CLIENT_ID,
        "software_id": "claude-code",
        "client_name": "Claude Code",
        "redirect_uris": ["http://localhost:8080/callback"],
        "scope": "memory.read memory.write",
        "registration_access_token_hash": _TOKEN_HASH,
        "disabled": False,
        "deleted_at": None,
    }
    base.update(overrides)
    return base


def _build(
    *,
    lookup: Any = None,
    db_deleter: Any = None,
    cognito_deleter: Any = None,
) -> tuple[TestClient, dict[str, Any]]:
    lookup = lookup or FakeLookup(_row())
    db_deleter = db_deleter or FakeDbDeleter()
    cognito_deleter = cognito_deleter or FakeCognitoDeleter()
    app = FastAPI()
    app.include_router(
        make_dcr_admin_router(
            lookup=lookup,
            db_deleter=db_deleter,
            cognito_deleter=cognito_deleter,
            resource_url=_RESOURCE,
        )
    )
    return TestClient(app), {"lookup": lookup, "db": db_deleter, "cognito": cognito_deleter}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class TestHelpers:
    def test_hash_token_deterministic(self) -> None:
        assert _hash_token(_TOKEN) == _TOKEN_HASH

    def test_verify_token_correct(self) -> None:
        assert _verify_token(_TOKEN, _TOKEN_HASH) is True

    def test_verify_token_wrong(self) -> None:
        assert _verify_token("wrong-token", _TOKEN_HASH) is False


# --------------------------------------------------------------------------
# GET — auth failures
# --------------------------------------------------------------------------


class TestGetAuthFailures:
    def test_missing_authorization_returns_401(self) -> None:
        client, _ = _build()
        resp = client.get(f"/oauth/register/{_CLIENT_ID}")
        assert resp.status_code == 401
        assert resp.json()["detail"]["reason"] == "missing_bearer"
        assert "Bearer" in resp.headers["WWW-Authenticate"]

    def test_non_bearer_scheme_returns_401(self) -> None:
        client, _ = _build()
        resp = client.get(
            f"/oauth/register/{_CLIENT_ID}",
            headers={"Authorization": "Basic xyz"},
        )
        assert resp.status_code == 401

    def test_unknown_client_id_returns_401(self) -> None:
        # Lookup returns None
        client, _ = _build(lookup=FakeLookup(None))
        resp = client.get(
            "/oauth/register/does-not-exist",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["reason"] == "unknown_or_wrong_token"

    def test_wrong_token_returns_401(self) -> None:
        client, _ = _build()
        resp = client.get(
            f"/oauth/register/{_CLIENT_ID}",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["reason"] == "unknown_or_wrong_token"


# --------------------------------------------------------------------------
# GET — success
# --------------------------------------------------------------------------


class TestGetSuccess:
    def test_returns_rfc7591_payload(self) -> None:
        client, _ = _build()
        resp = client.get(
            f"/oauth/register/{_CLIENT_ID}",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["client_id"] == _CLIENT_ID
        assert body["client_name"] == "Claude Code"
        assert body["redirect_uris"] == ["http://localhost:8080/callback"]
        assert body["scope"] == "memory.read memory.write"
        assert body["token_endpoint_auth_method"] == "none"
        assert body["registration_client_uri"] == f"{_RESOURCE}/oauth/register/{_CLIENT_ID}"

    def test_does_not_echo_token(self) -> None:
        client, _ = _build()
        resp = client.get(
            f"/oauth/register/{_CLIENT_ID}",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        # registration_access_token MUST NOT be echoed back
        assert "registration_access_token" not in resp.json()
        assert "registration_access_token_hash" not in resp.json()


# --------------------------------------------------------------------------
# DELETE — auth failures (same paths as GET)
# --------------------------------------------------------------------------


class TestDeleteAuthFailures:
    def test_missing_token_returns_401(self) -> None:
        client, _ = _build()
        resp = client.delete(f"/oauth/register/{_CLIENT_ID}")
        assert resp.status_code == 401

    def test_wrong_token_returns_401(self) -> None:
        client, _ = _build()
        resp = client.delete(
            f"/oauth/register/{_CLIENT_ID}",
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401


# --------------------------------------------------------------------------
# DELETE — success
# --------------------------------------------------------------------------


class TestDeleteSuccess:
    def test_204_on_success(self) -> None:
        client, deps = _build()
        resp = client.delete(
            f"/oauth/register/{_CLIENT_ID}",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert resp.status_code == 204
        assert resp.text == ""
        # Both deleters were called
        assert deps["db"].calls == [_CLIENT_ID]
        assert deps["cognito"].calls == [_CLIENT_ID]

    def test_cognito_failure_still_returns_204(self) -> None:
        """Local soft-delete is enough to revoke; Cognito retry is T-4.9."""
        cognito = FakeCognitoDeleter(error=RuntimeError("cognito down"))
        client, deps = _build(cognito_deleter=cognito)
        resp = client.delete(
            f"/oauth/register/{_CLIENT_ID}",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert resp.status_code == 204
        assert deps["db"].calls == [_CLIENT_ID]
        assert deps["cognito"].calls == [_CLIENT_ID]

    def test_already_deleted_still_returns_204(self) -> None:
        """Race: row already gone. Still succeed (idempotent)."""
        client, _ = _build(db_deleter=FakeDbDeleter(returns=False))
        resp = client.delete(
            f"/oauth/register/{_CLIENT_ID}",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert resp.status_code == 204
