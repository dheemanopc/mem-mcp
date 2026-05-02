"""Unit tests for the PreSignUp Lambda stub."""

from __future__ import annotations

import sys
from pathlib import Path

# Add lambda dir to path so we can import handler
_LAMBDA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LAMBDA_DIR))

import handler  # noqa: E402


def _mk_event(email: str = "user@example.com") -> dict:
    return {
        "version": "1",
        "region": "ap-south-1",
        "userPoolId": "ap-south-1_TEST",
        "userName": "TEST",
        "callerContext": {},
        "triggerSource": "PreSignUp_SignUp",
        "request": {
            "userAttributes": {"email": email},
            "validationData": None,
        },
        "response": {"autoConfirmUser": False, "autoVerifyEmail": False, "autoVerifyPhone": False},
    }


def test_stub_approves_signup() -> None:
    """The v1 stub should return the event unchanged (= approve)."""
    event = _mk_event()
    result = handler.lambda_handler(event, object())
    assert result == event


def test_hash_email_is_deterministic_and_short() -> None:
    h1 = handler._hash_email("user@example.com")
    h2 = handler._hash_email("USER@example.com")  # case-insensitive
    assert h1 == h2
    assert len(h1) == 12


def test_handler_does_not_crash_without_email() -> None:
    """Missing email shouldn't crash the handler — Cognito sometimes calls without it."""
    event = _mk_event()
    event["request"]["userAttributes"] = {}
    result = handler.lambda_handler(event, object())
    assert result == event
