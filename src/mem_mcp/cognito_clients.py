"""Production Cognito Protocol implementations using boto3 and httpx."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class CognitoTokens:
    """Tokens returned from Cognito token endpoint."""

    access_token: str
    id_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class CognitoUserInfo:
    """User info extracted from Cognito id_token."""

    cognito_sub: str
    cognito_username: str
    email: str
    provider: str  # 'google' or 'cognito'
    provider_user_id: str | None
    workspace_domain: str | None = None  # custom:google_hd for workspace users


class HttpxTokenExchanger:
    """Exchanges Cognito authorization code for tokens via /oauth2/token."""

    def __init__(self, *, cognito_token_url: str, client_id: str, client_secret: str) -> None:
        self._url = cognito_token_url
        self._client_id = client_id
        self._client_secret = client_secret

    async def exchange_code(self, code: str, redirect_uri: str) -> CognitoTokens:
        """Exchange authorization code for tokens."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                self._url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self._client_id,
                },
                auth=(self._client_id, self._client_secret),
            )
            resp.raise_for_status()
            d = resp.json()
            return CognitoTokens(
                access_token=d["access_token"],
                id_token=d["id_token"],
                refresh_token=d.get("refresh_token", ""),
                expires_in=int(d.get("expires_in", 3600)),
            )


class IdTokenUserInfoFetcher:
    """Decodes the Cognito id_token (JWT) for user info — no extra HTTP call needed."""

    async def get_user_info(self, id_token: str) -> CognitoUserInfo:
        """Extract user info from id_token JWT."""
        # id_token is a JWT; payload is base64url(json) in middle segment
        payload_b64 = id_token.split(".")[1]
        # Add padding
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))

        # Cognito identities[] is JSON-encoded string in the id_token
        identities_raw = payload.get("identities")
        provider = "cognito"
        provider_user_id = None
        if identities_raw:
            if isinstance(identities_raw, str):
                identities = json.loads(identities_raw)
            else:
                identities = identities_raw
            if identities:
                provider = identities[0].get("providerName", "cognito").lower()
                provider_user_id = identities[0].get("userId")

        # Extract custom:google_hd for workspace domain (enterprise feature)
        workspace_domain = payload.get("custom:google_hd")

        return CognitoUserInfo(
            cognito_sub=payload["sub"],
            cognito_username=payload.get("cognito:username", payload["sub"]),
            email=payload.get("email", ""),
            provider=provider,
            provider_user_id=provider_user_id,
            workspace_domain=workspace_domain,
        )


class BotoAdminDeleter:
    """Wraps cognito-idp admin-delete-user."""

    def __init__(self, *, user_pool_id: str, region: str = "ap-south-1") -> None:
        import boto3  # type: ignore

        self._client = boto3.client("cognito-idp", region_name=region)
        self._user_pool_id = user_pool_id

    async def admin_delete_user(self, cognito_username: str) -> None:
        """Delete a user from the Cognito user pool."""
        import asyncio

        await asyncio.to_thread(
            self._client.admin_delete_user,
            UserPoolId=self._user_pool_id,
            Username=cognito_username,
        )


class BotoGlobalSignOutter:
    """Wraps cognito-idp admin-user-global-sign-out."""

    def __init__(self, *, user_pool_id: str, region: str = "ap-south-1") -> None:
        import boto3

        self._client = boto3.client("cognito-idp", region_name=region)
        self._user_pool_id = user_pool_id

    async def admin_user_global_sign_out(self, cognito_username: str) -> None:
        """Sign out a user globally from all sessions."""
        import asyncio

        await asyncio.to_thread(
            self._client.admin_user_global_sign_out,
            UserPoolId=self._user_pool_id,
            Username=cognito_username,
        )


class BotoClientDeleter:
    """Wraps cognito-idp delete-user-pool-client."""

    def __init__(self, *, user_pool_id: str, region: str = "ap-south-1") -> None:
        import boto3

        self._client = boto3.client("cognito-idp", region_name=region)
        self._user_pool_id = user_pool_id

    async def delete_user_pool_client(self, client_id: str) -> None:
        """Delete an OAuth client from the Cognito user pool."""
        import asyncio

        await asyncio.to_thread(
            self._client.delete_user_pool_client,
            UserPoolId=self._user_pool_id,
            ClientId=client_id,
        )
