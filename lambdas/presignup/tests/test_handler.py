"""Unit tests for the PreSignUp Lambda real logic (T-4.8)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Add lambda dir to path so we can import handler
_LAMBDA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LAMBDA_DIR))


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_INVITE_URL", "https://memsys.test.example/internal/check_invite")
    monkeypatch.setenv("INTERNAL_INVITE_SECRET_SSM", "/mem-mcp/internal/lambda_secret")
    monkeypatch.setenv("AWS_REGION_NAME", "ap-south-1")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    # Reload the module so module-level globals pick up new env
    import importlib

    if "handler" in sys.modules:
        del sys.modules["handler"]
    import handler  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_secret_cache() -> None:
    """Clear the cached secret between tests."""
    import handler

    handler._cached_secret = None
    yield
    handler._cached_secret = None


def _mk_event(
    email: str = "user@example.com",
    *,
    identities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    user_attrs: dict[str, Any] = {"email": email} if email else {}
    if identities is not None:
        user_attrs["identities"] = json.dumps(identities)
    return {
        "version": "1",
        "region": "ap-south-1",
        "userPoolId": "ap-south-1_TEST",
        "userName": "TEST",
        "callerContext": {},
        "triggerSource": "PreSignUp_SignUp",
        "request": {"userAttributes": user_attrs, "validationData": None},
        "response": {"autoConfirmUser": False, "autoVerifyEmail": False, "autoVerifyPhone": False},
    }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class TestHashEmail:
    def test_deterministic_and_short(self) -> None:
        import handler

        h1 = handler._hash_email("user@example.com")
        h2 = handler._hash_email("USER@example.com")
        assert h1 == h2
        assert len(h1) == 12


class TestExtractProvider:
    def test_no_identities(self) -> None:
        import handler

        assert handler._extract_provider({}) is None

    def test_string_identities(self) -> None:
        import handler

        attrs = {"identities": json.dumps([{"providerName": "Google"}])}
        assert handler._extract_provider(attrs) == "google"

    def test_list_identities(self) -> None:
        import handler

        attrs = {"identities": [{"providerName": "Apple"}]}
        assert handler._extract_provider(attrs) == "apple"

    def test_malformed_identities(self) -> None:
        import handler

        attrs = {"identities": "not-json"}
        assert handler._extract_provider(attrs) is None

    def test_empty_list(self) -> None:
        import handler

        attrs = {"identities": []}
        assert handler._extract_provider(attrs) is None


# --------------------------------------------------------------------------
# _post_check_invite (mock httpx)
# --------------------------------------------------------------------------


class TestPostCheckInvite:
    def test_allow_response_returned(self) -> None:
        import handler

        mock_response = MagicMock()
        mock_response.json.return_value = {"decision": "allow", "reason": "invited"}
        mock_response.raise_for_status.return_value = None

        with patch.object(handler, "_load_secret", return_value="test-secret"), \
             patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = handler._post_check_invite("u@e.com", None)

        assert result == {"decision": "allow", "reason": "invited"}
        # Check the call was made with HMAC header
        call_kwargs = mock_client.post.call_args.kwargs
        assert "X-Internal-Auth" in call_kwargs["headers"]
        assert call_kwargs["headers"]["Content-Type"] == "application/json"

    def test_provider_included_in_payload(self) -> None:
        import handler

        mock_response = MagicMock()
        mock_response.json.return_value = {"decision": "allow", "reason": "invited"}
        mock_response.raise_for_status.return_value = None

        with patch.object(handler, "_load_secret", return_value="test-secret"), \
             patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            handler._post_check_invite("u@e.com", "google")

        body = mock_client.post.call_args.kwargs["content"]
        parsed = json.loads(body)
        assert parsed == {"email": "u@e.com", "provider": "google"}

    def test_no_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import handler

        monkeypatch.setattr(handler, "_INTERNAL_INVITE_URL", "")
        with pytest.raises(RuntimeError, match="INTERNAL_INVITE_URL"):
            handler._post_check_invite("u@e.com", None)


# --------------------------------------------------------------------------
# lambda_handler — happy path
# --------------------------------------------------------------------------


class TestLambdaHandlerAllow:
    def test_allow_returns_event(self) -> None:
        import handler

        with patch.object(
            handler, "_post_check_invite", return_value={"decision": "allow", "reason": "invited"}
        ):
            event = _mk_event()
            result = handler.lambda_handler(event, object())
        assert result is event

    def test_passes_provider_from_identities(self) -> None:
        import handler

        captured: dict[str, Any] = {}

        def fake_post(email: str, provider: str | None) -> dict[str, Any]:
            captured["email"] = email
            captured["provider"] = provider
            return {"decision": "allow", "reason": "invited"}

        with patch.object(handler, "_post_check_invite", side_effect=fake_post):
            event = _mk_event(identities=[{"providerName": "Google"}])
            handler.lambda_handler(event, object())

        assert captured == {"email": "user@example.com", "provider": "google"}


# --------------------------------------------------------------------------
# lambda_handler — deny
# --------------------------------------------------------------------------


class TestLambdaHandlerDeny:
    def test_deny_raises(self) -> None:
        import handler

        with patch.object(
            handler, "_post_check_invite", return_value={"decision": "deny", "reason": "not_invited"}
        ):
            event = _mk_event()
            with pytest.raises(RuntimeError, match="not currently available"):
                handler.lambda_handler(event, object())

    def test_deny_already_consumed_raises(self) -> None:
        import handler

        with patch.object(
            handler, "_post_check_invite", return_value={"decision": "deny", "reason": "already_consumed"}
        ):
            event = _mk_event()
            with pytest.raises(RuntimeError):
                handler.lambda_handler(event, object())


# --------------------------------------------------------------------------
# lambda_handler — fail-closed
# --------------------------------------------------------------------------


class TestLambdaHandlerFailClosed:
    def test_http_error_denies(self) -> None:
        import handler

        with patch.object(handler, "_post_check_invite", side_effect=RuntimeError("network down")):
            event = _mk_event()
            with pytest.raises(RuntimeError, match="invite check unavailable"):
                handler.lambda_handler(event, object())

    def test_missing_email_raises(self) -> None:
        import handler

        event = _mk_event(email="")
        with pytest.raises(RuntimeError, match="missing email"):
            handler.lambda_handler(event, object())


# --------------------------------------------------------------------------
# _load_secret (cached)
# --------------------------------------------------------------------------


class TestLoadSecretCaching:
    def test_first_call_reads_ssm_subsequent_uses_cache(self) -> None:
        import handler

        mock_response = {"Parameter": {"Value": "secret-from-ssm"}}
        mock_client = MagicMock()
        mock_client.get_parameter.return_value = mock_response

        with patch("boto3.client", return_value=mock_client):
            s1 = handler._load_secret()
            s2 = handler._load_secret()
            s3 = handler._load_secret()

        assert s1 == s2 == s3 == "secret-from-ssm"
        # boto3 client.get_parameter called only once (cached)
        assert mock_client.get_parameter.call_count == 1

    def test_empty_secret_raises(self) -> None:
        import handler

        mock_response = {"Parameter": {"Value": ""}}
        mock_client = MagicMock()
        mock_client.get_parameter.return_value = mock_response

        with patch("boto3.client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="empty SSM secret"):
                handler._load_secret()
