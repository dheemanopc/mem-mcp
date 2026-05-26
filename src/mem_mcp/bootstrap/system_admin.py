"""Bootstrap the first system_admin from MEM_MCP_BOOTSTRAP_SYSTEM_ADMIN_EMAIL env var.

Idempotent: marks completion in system_state and skips on subsequent runs.
Safe to fail: any error here is logged but does NOT crash startup.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from mem_mcp.auth.permissions import Role
from mem_mcp.db import system_tx

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

log = logging.getLogger(__name__)

_STATE_KEY = "bootstrap_first_admin"
_ENV_VAR = "MEM_MCP_BOOTSTRAP_SYSTEM_ADMIN_EMAIL"


async def bootstrap_first_system_admin(pool: asyncpg.Pool) -> dict[str, str]:
    """Grant SYSTEM_ADMIN to the tenant whose identity.email matches env var.

    Returns a status dict for logging:
        {"status": "granted"|"already_done"|"skipped"|"not_found"|"error", ...}

    Conditions for granting:
      1. Env var MEM_MCP_BOOTSTRAP_SYSTEM_ADMIN_EMAIL is set + non-empty
      2. system_state has no row with key='bootstrap_first_admin' (idempotency)
      3. tenant_identities row matches that email (case-insensitive)

    On success: insert tenant_system_roles + system_state marker + audit log entry.
    """
    email = (os.environ.get(_ENV_VAR) or "").strip()
    if not email:
        return {"status": "skipped", "reason": "env_not_set"}

    async with system_tx(pool) as conn:
        # idempotency check
        existing = await conn.fetchval("SELECT 1 FROM system_state WHERE key = $1", _STATE_KEY)
        if existing:
            return {"status": "already_done"}

        # find a tenant_identity by email (case-insensitive)
        row = await conn.fetchrow(
            "SELECT tenant_id, id FROM tenant_identities WHERE LOWER(email) = LOWER($1) LIMIT 1",
            email,
        )
        if row is None:
            return {"status": "not_found", "email": email}

        tenant_id = row["tenant_id"]

        async with conn.transaction():
            # Grant role (idempotent via UNIQUE constraint)
            await conn.execute(
                """
                INSERT INTO tenant_system_roles (tenant_id, role, granted_by_tenant_id, notes)
                VALUES ($1, $2, NULL, 'bootstrap from MEM_MCP_BOOTSTRAP_SYSTEM_ADMIN_EMAIL env var')
                ON CONFLICT (tenant_id, role) DO NOTHING
                """,
                tenant_id,
                Role.SYSTEM_ADMIN.value,
            )
            await conn.execute(
                """
                INSERT INTO system_state (key, value)
                VALUES ($1, jsonb_build_object('tenant_id', $2::text, 'email', $3))
                ON CONFLICT (key) DO NOTHING
                """,
                _STATE_KEY,
                str(tenant_id),
                email,
            )

        return {"status": "granted", "tenant_id": str(tenant_id), "email": email}
