"""Cognito PreSignUp Lambda for mem-mcp.

v1 role per LLD §0:
  - Check the invited_emails allowlist via internal HTTPS endpoint
    POST https://memsys.<domain>/internal/check_invite
  - Allow or deny the Cognito sign-up.
  - No tenant creation here — that happens in /auth/callback (T-8.2).
  - No link-mode awareness, no email-collision detection (Google-only IdP).

Env vars (set by 050-lambda-presignup.yaml):
  INTERNAL_INVITE_URL          full URL to memsys.<domain>/internal/check_invite
  INTERNAL_INVITE_SECRET_SSM   SSM SecureString param name (default /mem-mcp/internal/lambda_secret)
  AWS_REGION_NAME              region (Lambda's default AWS_REGION reserved by runtime)
  LOG_LEVEL                    INFO | DEBUG | WARNING (default INFO)

Fail-closed: on HTTP / SSM errors we DENY the signup. Better to lock out
a legitimate user than to let an unverified one through.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from typing import Any

# Module-level config (cold-start cache)
_INTERNAL_INVITE_URL = os.environ.get("INTERNAL_INVITE_URL", "")
_SECRET_SSM_NAME = os.environ.get(
    "INTERNAL_INVITE_SECRET_SSM",
    "/mem-mcp/internal/lambda_secret",
)
_REGION = os.environ.get("AWS_REGION_NAME") or os.environ.get("AWS_REGION", "ap-south-1")
_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
_HTTP_TIMEOUT_SECONDS = 4.0  # PreSignUp has 5s Cognito-side timeout

# Lazy-cached secret (filled on first call; survives across warm invocations)
_cached_secret: str | None = None


def _log(level: str, **kw: Any) -> None:
    """Structured JSON to stdout. Cognito captures Lambda stdout to CloudWatch."""
    if _level_rank(level) < _level_rank(_LOG_LEVEL):
        return
    payload = {"timestamp": time.time(), "level": level.lower(), **kw}
    print(json.dumps(payload), file=sys.stdout, flush=True)


def _level_rank(name: str) -> int:
    return {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}.get(name.upper(), 20)


def _hash_email(email: str) -> str:
    """Non-reversible 12-char identifier for log correlation only."""
    return hashlib.sha256(email.lower().encode("utf-8")).hexdigest()[:12]


def _load_secret() -> str:
    """Read SecureString from SSM. Cached after first successful read."""
    global _cached_secret
    if _cached_secret is not None:
        return _cached_secret

    # Lazy import — keeps cold-start light if Lambda environment isn't fully wired
    import boto3  # type: ignore[import-untyped]

    client = boto3.client("ssm", region_name=_REGION)
    response = client.get_parameter(Name=_SECRET_SSM_NAME, WithDecryption=True)
    secret = response["Parameter"]["Value"]
    if not isinstance(secret, str) or not secret:
        raise RuntimeError(f"empty SSM secret at {_SECRET_SSM_NAME}")
    _cached_secret = secret
    return secret


def _post_check_invite(email: str, provider: str | None) -> dict[str, Any]:
    """POST /internal/check_invite with HMAC. Returns parsed JSON.

    Raises: any HTTP error, JSON decode error, etc.
    """
    if not _INTERNAL_INVITE_URL:
        raise RuntimeError("INTERNAL_INVITE_URL env var not set")

    # Lazy import
    import httpx

    payload = {"email": email}
    if provider:
        payload["provider"] = provider
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    secret = _load_secret()
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        resp = client.post(
            _INTERNAL_INVITE_URL,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Internal-Auth": sig,
            },
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Cognito PreSignUp trigger handler.

    Returning the event unchanged → approve signup.
    Raising any exception → deny (Cognito surfaces a generic message).
    """
    user_attrs = event.get("request", {}).get("userAttributes", {})
    email = user_attrs.get("email", "")
    if not email:
        _log("WARNING", event="presignup_missing_email", trigger=event.get("triggerSource"))
        raise RuntimeError("PreSignUp event missing email")

    # Cognito's "identities" attribute (JSON-string list) carries the IdP name when federated
    provider = _extract_provider(user_attrs)

    try:
        result = _post_check_invite(email, provider)
    except Exception as exc:
        _log(
            "ERROR",
            event="presignup_check_invite_failed",
            email_hash=_hash_email(email),
            error=str(exc)[:300],
        )
        # Fail closed: deny rather than allow on infrastructure failure
        raise RuntimeError("invite check unavailable; signup denied") from exc

    decision = str(result.get("decision", "")).lower()
    reason = str(result.get("reason", ""))

    _log(
        "INFO",
        event="presignup_decision",
        email_hash=_hash_email(email),
        provider=provider,
        decision=decision,
        reason=reason,
    )

    if decision == "allow":
        return event

    # deny — raise a generic message so we don't leak invite status to attackers
    raise RuntimeError("Sign-up not currently available for this email")


def _extract_provider(user_attrs: dict[str, Any]) -> str | None:
    """Cognito puts federated IdP info into 'identities' as a JSON string of objects.
    Return the providerName lowercased (e.g., 'google'), or None if not federated.
    """
    identities_raw = user_attrs.get("identities")
    if not identities_raw:
        return None
    try:
        identities = (
            json.loads(identities_raw) if isinstance(identities_raw, str) else identities_raw
        )
    except (ValueError, TypeError):
        return None
    if not isinstance(identities, list) or not identities:
        return None
    first = identities[0]
    if not isinstance(first, dict):
        return None
    name = first.get("providerName")
    return name.lower() if isinstance(name, str) else None
