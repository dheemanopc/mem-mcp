"""Reusable Kite credential storage + smoke-test helper.

Used by:
- memsys_enable_kite MCP tool (manual / request_token / Mode C onboarding)
- /api/web/skills/kite/confirm route (Option B intent handoff)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]

from mem_mcp.skills.kite.auth import KiteAuthError, login_with_credentials
from mem_mcp.skills.vault import SkillVault


@dataclass
class EnableKiteResult:
    mode: Literal["manual", "request_token_exchange", "auto_login"]
    kite_user_id: str | None
    smoke_test: Literal["ok", "skipped", "failed"]


class KiteOnboardingError(Exception):
    """Raised when the smoke test fails or creds are inconsistent."""


async def enable_kite_for_tenant(
    *,
    conn: asyncpg.Connection,
    tenant_id: UUID,
    creds: dict[str, Any],
    vault: SkillVault,
    run_smoke_test: bool = True,
) -> EnableKiteResult:
    """Store the given creds in the tenant's skill vault.

    Two valid input shapes:

    1. Manual mode: creds carry api_key + api_secret + access_token. Smoke test
       is always skipped. Caller (memsys_enable_kite Mode A) has already
       validated the access_token; we just persist.

    2. Auto-login mode: creds carry api_key + api_secret + user_id + password +
       totp_secret (and may or may not include access_token). When
       run_smoke_test=True we run the full 5-step Zerodha login to validate
       the credentials AND obtain a fresh access_token, which is stitched
       into the stored payload. This is the path the Option B confirm route
       takes — algotrade pushes the trio (no access_token), and memsys
       acquires it here at confirm time.

    Raises KiteOnboardingError if validation or smoke-test fails.
    """
    api_key = creds.get("api_key")
    api_secret = creds.get("api_secret")
    if not api_key or not api_secret:
        raise KiteOnboardingError("api_key and api_secret are required")

    user_id = creds.get("user_id")
    password = creds.get("password")
    totp_secret = creds.get("totp_secret")
    access_token = creds.get("access_token")
    has_auto_login = bool(user_id and password and totp_secret)

    if has_auto_login and run_smoke_test:
        assert user_id is not None and password is not None and totp_secret is not None
        try:
            result = await login_with_credentials(
                api_key=api_key,
                api_secret=api_secret,
                user_id=user_id,
                password=password,
                totp_secret=totp_secret,
            )
        except KiteAuthError as exc:
            raise KiteOnboardingError(f"auto-login smoke test failed: {exc}") from exc
        new_token = result.get("access_token")
        if not new_token:
            raise KiteOnboardingError("auto-login returned no access_token")
        creds["access_token"] = new_token
        if result.get("kite_user_id"):
            creds["kite_user_id"] = result["kite_user_id"]
        mode: Literal["manual", "request_token_exchange", "auto_login"] = "auto_login"
        smoke: Literal["ok", "skipped", "failed"] = "ok"
    elif has_auto_login:
        mode = "auto_login"
        smoke = "skipped"
    elif access_token:
        mode = "manual"
        smoke = "skipped"
    else:
        raise KiteOnboardingError(
            "creds must include access_token OR the full auto-login trio (user_id+password+totp_secret)"
        )

    creds.setdefault("orders_enabled", False)
    await vault.store(conn, tenant_id, "kite", creds)
    return EnableKiteResult(
        mode=mode,
        kite_user_id=creds.get("kite_user_id"),
        smoke_test=smoke,
    )
