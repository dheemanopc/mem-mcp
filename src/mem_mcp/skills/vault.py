"""SkillVault — AES-256-GCM envelope encryption for per-tenant per-skill credentials.

Master key sourced from settings.skill_vault_master_key (base64-encoded 32 bytes).
Cached at app startup (one SSM/KMS Decrypt at boot). Per-call encrypt/decrypt is
pure local AES — no AWS API calls in the hot path.

Storage format: ``nonce(12B) || ciphertext_with_tag``.
``ciphertext_with_tag`` is the output of ``AESGCM.encrypt`` which appends a 16-byte
authentication tag to the ciphertext.

Associated data: ``f"{tenant_id}:{skill_name}".encode()`` — binds each ciphertext
to its row. A row's ciphertext cannot be relocated to a different (tenant, skill)
pair without invalidating the GCM tag.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any, cast
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mem_mcp.config import Settings
from mem_mcp.skills.base import SkillCredentialsMissingError

NONCE_LEN = 12
TAG_LEN = 16
MIN_BLOB_LEN = NONCE_LEN + TAG_LEN


class SkillVaultError(Exception):
    """Vault-layer error (decryption failure, missing key, malformed ciphertext)."""


class SkillVaultDisabledError(SkillVaultError):
    """Raised when SkillVault is constructed without a master key."""


class SkillVault:
    """Per-tenant per-skill credential encryptor/decryptor + DB persistence."""

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != 32:
            raise SkillVaultError(f"master_key must be exactly 32 bytes, got {len(master_key)}")
        self._aesgcm = AESGCM(master_key)

    @classmethod
    def from_settings(cls, settings: Settings) -> SkillVault:
        if not settings.skill_vault_master_key:
            raise SkillVaultDisabledError(
                "skill_vault_master_key is unset; skill framework cannot start"
            )
        try:
            key = base64.b64decode(settings.skill_vault_master_key, validate=True)
        except Exception as exc:
            raise SkillVaultError("skill_vault_master_key is not valid base64") from exc
        return cls(key)

    def _aad(self, tenant_id: UUID, skill_name: str) -> bytes:
        return f"{tenant_id}:{skill_name}".encode()

    def encrypt(self, plaintext: bytes, aad: bytes) -> bytes:
        nonce = os.urandom(NONCE_LEN)
        ciphertext_with_tag = self._aesgcm.encrypt(nonce, plaintext, aad)
        return nonce + ciphertext_with_tag

    def decrypt(self, blob: bytes, aad: bytes) -> bytes:
        if len(blob) < MIN_BLOB_LEN:
            raise SkillVaultError(f"ciphertext blob too short: got {len(blob)}, min {MIN_BLOB_LEN}")
        nonce = blob[:NONCE_LEN]
        ciphertext_with_tag = blob[NONCE_LEN:]
        try:
            return self._aesgcm.decrypt(nonce, ciphertext_with_tag, aad)
        except Exception as exc:
            raise SkillVaultError("credentials decryption failed (tag mismatch)") from exc

    def encrypt_creds(self, tenant_id: UUID, skill_name: str, creds: dict[str, Any]) -> bytes:
        plaintext = json.dumps(creds, separators=(",", ":"), sort_keys=True).encode()
        return self.encrypt(plaintext, self._aad(tenant_id, skill_name))

    def decrypt_creds(self, tenant_id: UUID, skill_name: str, blob: bytes) -> dict[str, Any]:
        plaintext = self.decrypt(blob, self._aad(tenant_id, skill_name))
        return cast(dict[str, Any], json.loads(plaintext.decode()))

    async def store(
        self,
        conn: asyncpg.Connection,
        tenant_id: UUID,
        skill_name: str,
        creds: dict[str, Any],
    ) -> None:
        """Upsert encrypted credentials for (tenant_id, skill_name)."""
        ciphertext = self.encrypt_creds(tenant_id, skill_name, creds)
        await conn.execute(
            """
            INSERT INTO tenant_skill_credentials
                (tenant_id, skill_name, credentials_ciphertext,
                 schema_version, created_at, updated_at, last_refreshed_at)
            VALUES ($1, $2, $3, 1, now(), now(), now())
            ON CONFLICT (tenant_id, skill_name) DO UPDATE
            SET credentials_ciphertext = EXCLUDED.credentials_ciphertext,
                updated_at = now(),
                last_refreshed_at = now()
            """,
            tenant_id,
            skill_name,
            ciphertext,
        )

    async def load(
        self,
        conn: asyncpg.Connection,
        tenant_id: UUID,
        skill_name: str,
    ) -> dict[str, Any]:
        """Fetch and decrypt credentials. Raises SkillCredentialsMissing if absent."""
        row = await conn.fetchrow(
            """
            SELECT credentials_ciphertext
            FROM tenant_skill_credentials
            WHERE tenant_id = $1 AND skill_name = $2
            """,
            tenant_id,
            skill_name,
        )
        if row is None:
            raise SkillCredentialsMissingError(
                f"no credentials stored for tenant {tenant_id} skill {skill_name!r}"
            )
        return self.decrypt_creds(tenant_id, skill_name, bytes(row["credentials_ciphertext"]))

    async def delete(
        self,
        conn: asyncpg.Connection,
        tenant_id: UUID,
        skill_name: str,
    ) -> bool:
        """Remove credentials row. Returns True if a row was deleted."""
        result = await conn.execute(
            """
            DELETE FROM tenant_skill_credentials
            WHERE tenant_id = $1 AND skill_name = $2
            """,
            tenant_id,
            skill_name,
        )
        return cast(bool, result.endswith(" 1"))
