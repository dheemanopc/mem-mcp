"""Cognito PreSignUp Lambda for mem-mcp.

v1 role (per LLD §0):
- Check `invited_emails` allowlist via the internal HTTPS endpoint at memsys.<domain>/internal/check_invite.
- Allow or deny the signup.
- Tenant creation moved to /auth/callback (NOT here) — this Lambda has no DB access.

This file is a STUB. The real implementation lands in T-4.8.
For now it allows all signups — DO NOT enable this in production until T-4.8 wires the real check.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL, format="%(message)s")
logger = logging.getLogger(__name__)


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Cognito PreSignUp trigger handler.

    STUB: currently approves all signups. Real invite-check lands in T-4.8.

    Cognito invokes this synchronously during the sign-up flow.
    Returning the event unchanged = approve.
    Raising an exception = deny (Cognito surfaces the message to the user).
    """
    email = event.get("request", {}).get("userAttributes", {}).get("email", "<missing>")

    logger.info(
        json.dumps(
            {
                "event": "presignup_invoked",
                "email_hash": _hash_email(email),  # never log raw email
                "trigger": event.get("triggerSource"),
                "user_pool_id": event.get("userPoolId"),
                "decision": "STUB_ALLOW",
                "warning": "T-4.8 not yet wired; allowing all signups",
            }
        )
    )

    # STUB: approve. Real T-4.8 will call /internal/check_invite and decide.
    return event


def _hash_email(email: str) -> str:
    """Return a short non-reversible identifier for an email, for log correlation only."""
    return hashlib.sha256(email.lower().encode("utf-8")).hexdigest()[:12]
