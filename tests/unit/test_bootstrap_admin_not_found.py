"""Regression-lock for bootstrap chicken-and-egg path.

BS-1 per R-LOCALDEV §3.1: when MEM_MCP_BOOTSTRAP_SYSTEM_ADMIN_EMAIL is set
but no tenant_identities row matches, bootstrap_first_system_admin returns
{status: "not_found"} without crashing AND does NOT insert a system_state
row. Idempotent re-run will retry on next boot (correct behavior — admin
hasn't signed in yet).
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from mem_mcp.bootstrap.system_admin import bootstrap_first_system_admin

pytestmark = pytest.mark.asyncio


def _mock_pool_with_no_identity() -> Any:
    """Build a pool/system_tx mock where:

    - system_state.bootstrap_first_admin lookup → None (not done)
    - tenant_identities lookup by email → None (chicken-and-egg: not signed in yet)
    """
    conn = MagicMock()
    # fetchval('SELECT 1 FROM system_state...') → None
    # fetchrow('SELECT tenant_id, id FROM tenant_identities...') → None
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)
    conn.transaction = MagicMock()
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)

    # system_tx(pool) is an async context manager yielding the conn
    sys_tx_cm = MagicMock()
    sys_tx_cm.__aenter__ = AsyncMock(return_value=conn)
    sys_tx_cm.__aexit__ = AsyncMock(return_value=None)

    pool = MagicMock()
    return pool, sys_tx_cm, conn


class TestBootstrapNotFound:
    async def test_bs1_returns_not_found_when_no_identity_matches(self) -> None:
        env_email = f"admin-{uuid4()}@local"
        pool, sys_tx_cm, conn = _mock_pool_with_no_identity()

        with patch.dict(
            os.environ, {"MEM_MCP_BOOTSTRAP_SYSTEM_ADMIN_EMAIL": env_email}, clear=False
        ):
            with patch("mem_mcp.bootstrap.system_admin.system_tx", return_value=sys_tx_cm):
                result = await bootstrap_first_system_admin(pool)

        assert result["status"] == "not_found"
        assert result["email"] == env_email
        # NO system_state insertion — only happens on 'granted' path
        # (verified by checking conn.execute was NOT called for the INSERT)
        for call in conn.execute.call_args_list:
            sql = call.args[0] if call.args else ""
            assert (
                "INSERT INTO system_state" not in sql
            ), "system_state must NOT be inserted on not_found"

    async def test_skipped_when_env_var_unset(self) -> None:
        pool, _, _ = _mock_pool_with_no_identity()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEM_MCP_BOOTSTRAP_SYSTEM_ADMIN_EMAIL", None)
            result = await bootstrap_first_system_admin(pool)
        assert result == {"status": "skipped", "reason": "env_not_set"}
