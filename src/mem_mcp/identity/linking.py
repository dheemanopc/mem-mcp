"""Identity linking helpers (T-7.10).

Manages the HMAC-signed state flow for secondary identity linking.
- start_link: Generate state, INSERT link_state, return authorize URL
- complete_link: Verify state, exchange code, INSERT tenant_identities
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from mem_mcp.db.tenant_tx import tenant_tx

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


@dataclass(frozen=True)
class CognitoTokens:
    """Tokens returned from Cognito token endpoint."""

    access_token: str
    id_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class CognitoUserInfo:
    """User info extracted from Cognito id_token or userinfo endpoint."""

    cognito_sub: str
    cognito_username: str
    email: str
    provider: str
    provider_user_id: str


class CognitoTokenExchanger(Protocol):
    """Wraps the Cognito Hosted UI token endpoint."""

    async def exchange_code(self, code: str, redirect_uri: str) -> CognitoTokens: ...


class CognitoUserInfoFetcher(Protocol):
    """Pulls userinfo from the id_token."""

    async def get_user_info(self, id_token: str) -> CognitoUserInfo: ...


@dataclass(frozen=True)
class StartLinkResult:
    """Result of start_link."""

    authorize_url: str
    signed_state: str
    nonce: str


@dataclass(frozen=True)
class CompleteLinkResult:
    """Result of complete_link."""

    identity_id: UUID


class LinkingError(Exception):
    """Raised when linking fails. code discriminates the failure mode."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


def sign_state(payload: dict[str, Any], secret: str) -> str:
    """HMAC-sign a JSON payload.

    Returns: base64(payload).hex(hmac_sha256(payload))
    """
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    payload_b64 = base64.b64encode(payload_json.encode()).decode()

    sig_bytes = hmac.new(secret.encode(), payload_json.encode(), hashlib.sha256).digest()
    sig_hex = sig_bytes.hex()

    return f"{payload_b64}.{sig_hex}"


def verify_state(signed: str, secret: str) -> dict[str, Any] | None:
    """Verify HMAC and return payload, or None if invalid/tampered."""
    try:
        parts = signed.rsplit(".", 1)
        if len(parts) != 2:
            return None

        payload_b64, sig_hex = parts
        payload_json = base64.b64decode(payload_b64).decode()
        payload: dict[str, Any] = json.loads(payload_json)

        # Constant-time comparison
        expected_sig = hmac.new(secret.encode(), payload_json.encode(), hashlib.sha256).digest()
        expected_sig_hex = expected_sig.hex()

        if not hmac.compare_digest(sig_hex, expected_sig_hex):
            return None

        return payload
    except Exception:
        return None


async def start_link(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    *,
    hmac_secret: str,
    cognito_authorize_base_url: str,
    cognito_client_id: str,
    redirect_uri: str,
    ttl_seconds: int = 600,
) -> StartLinkResult:
    """Create HMAC-signed state + nonce, INSERT into link_state, return authorize URL.

    The nonce is stored in link_state; the signed_state is returned to the web
    handler (which sets it as a cookie). On callback, both must match.
    """
    nonce = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl_seconds)

    payload = {
        "tenant_id": str(tenant_id),
        "nonce": nonce,
        "exp": int(expires_at.timestamp()),
    }
    signed_state = sign_state(payload, hmac_secret)

    async with tenant_tx(pool, tenant_id) as conn:
        await conn.execute(
            """
            INSERT INTO link_state (nonce, tenant_id, expires_at)
            VALUES ($1, $2, $3)
            """,
            nonce,
            tenant_id,
            expires_at,
        )

    authorize_url = (
        f"{cognito_authorize_base_url}?"
        f"client_id={cognito_client_id}&"
        f"response_type=code&"
        f"scope=openid+email+profile&"
        f"redirect_uri={redirect_uri}&"
        f"state={signed_state}"
    )

    return StartLinkResult(authorize_url=authorize_url, signed_state=signed_state, nonce=nonce)


async def complete_link(
    pool: asyncpg.Pool,
    *,
    signed_state: str,
    cookie_nonce: str,
    session_tenant_id: UUID,
    code: str,
    hmac_secret: str,
    redirect_uri: str,
    token_exchanger: CognitoTokenExchanger,
    user_info_fetcher: CognitoUserInfoFetcher,
    audit: Any,  # AuditLogger Protocol
    request_id: str,
) -> CompleteLinkResult:
    """Verify state HMAC + cookie nonce + session ownership, exchange code,
    INSERT tenant_identities, mark link_state consumed, audit.

    Raises LinkingError(code=...) for failure modes:
    - 'invalid_state': HMAC bad / signed payload missing fields
    - 'cookie_mismatch': cookie nonce != state nonce
    - 'session_mismatch': session.tenant_id != state.tenant_id
    - 'expired': link_state expired_at < now
    - 'consumed': link_state already consumed
    - 'sub_already_linked': cognito_sub already in tenant_identities
    - 'state_not_found': link_state row not found
    """
    # Verify HMAC signature
    payload = verify_state(signed_state, hmac_secret)
    if payload is None:
        raise LinkingError("invalid_state", "HMAC verification failed or malformed state")

    if not isinstance(payload.get("tenant_id"), str) or not isinstance(payload.get("nonce"), str):
        raise LinkingError("invalid_state", "Missing required fields in state payload")

    state_tenant_id = UUID(payload["tenant_id"])
    state_nonce = payload["nonce"]

    # Verify cookie nonce matches state nonce
    if not hmac.compare_digest(cookie_nonce, state_nonce):
        raise LinkingError("cookie_mismatch", "Cookie nonce does not match state nonce")

    # Verify session tenant matches state tenant
    if session_tenant_id != state_tenant_id:
        raise LinkingError("session_mismatch", "Session tenant does not match state tenant")

    # Fetch and validate link_state
    async with tenant_tx(pool, state_tenant_id) as conn:
        link_state_row = await conn.fetchrow(
            """
            SELECT nonce, tenant_id, expires_at, consumed_at
            FROM link_state
            WHERE nonce = $1
            """,
            state_nonce,
        )

        if link_state_row is None:
            raise LinkingError("state_not_found", "Link state not found in database")

        if link_state_row["consumed_at"] is not None:
            raise LinkingError("consumed", "Link state already consumed")

        now = datetime.now(UTC)
        if link_state_row["expires_at"] < now:
            raise LinkingError("expired", "Link state expired")

        # Exchange code for tokens
        tokens = await token_exchanger.exchange_code(code, redirect_uri)
        user_info = await user_info_fetcher.get_user_info(tokens.id_token)

        # Check if cognito_sub already linked
        existing = await conn.fetchval(
            """
            SELECT id FROM tenant_identities
            WHERE cognito_sub = $1
            """,
            user_info.cognito_sub,
        )

        if existing is not None:
            raise LinkingError("sub_already_linked", "Cognito sub already linked to a tenant")

        # INSERT tenant_identities
        try:
            identity_id = await conn.fetchval(
                """
                INSERT INTO tenant_identities (
                    tenant_id, cognito_sub, cognito_username, provider,
                    provider_user_id, email, is_primary
                )
                VALUES ($1, $2, $3, $4, $5, $6, false)
                RETURNING id
                """,
                state_tenant_id,
                user_info.cognito_sub,
                user_info.cognito_username,
                user_info.provider,
                user_info.provider_user_id,
                user_info.email,
            )
        except Exception as e:
            if "unique constraint" in str(e).lower():
                raise LinkingError("sub_already_linked", "Cognito sub already exists") from e
            raise

        # Mark link_state consumed
        await conn.execute(
            """
            UPDATE link_state
            SET consumed_at = now()
            WHERE nonce = $1
            """,
            state_nonce,
        )

        # Audit
        await audit.audit(
            conn,
            action="identity.linked",
            result="success",
            tenant_id=state_tenant_id,
            identity_id=identity_id,
            request_id=request_id,
            details={"cognito_sub": user_info.cognito_sub, "email": user_info.email},
        )

    return CompleteLinkResult(identity_id=identity_id)
