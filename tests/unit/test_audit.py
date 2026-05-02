"""Tests for mem_mcp.audit.logger (T-5.12)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from mem_mcp.audit.logger import (
    AUDIT_ACTIONS,
    DbAuditLogger,
    NoopAuditLogger,
)

# --------------------------------------------------------------------------
# AUDIT_ACTIONS
# --------------------------------------------------------------------------


class TestAuditActions:
    def test_completeness_per_spec(self) -> None:
        # FR-5.5.5 lists at least these — verify each is present
        required = {
            "auth.token_issued",
            "auth.token_refresh",
            "auth.token_refresh_reuse",
            "auth.token_revoked",
            "auth.session_started",
            "oauth.dcr_register",
            "oauth.dcr_rejected",
            "oauth.client_revoked",
            "tenant.created",
            "tenant.suspended",
            "tenant.deletion_requested",
            "tenant.deletion_cancelled",
            "tenant.deleted",
            "identity.linked",
            "identity.unlinked",
            "memory.write",
            "memory.search",
            "memory.get",
            "memory.list",
            "memory.update",
            "memory.delete",
            "memory.undelete",
            "memory.supersede",
            "memory.export",
            "memory.feedback",
            "memory.dedupe_merged",
            "quota.exceeded",
            "ratelimit.exceeded",
        }
        present = set(AUDIT_ACTIONS)
        missing = required - present
        assert not missing, f"missing audit actions: {missing}"


# --------------------------------------------------------------------------
# DbAuditLogger
# --------------------------------------------------------------------------


class TestDbAuditLogger:
    @pytest.mark.asyncio
    async def test_inserts_with_all_columns(self) -> None:
        conn = AsyncMock()
        logger = DbAuditLogger()
        tenant_id = uuid4()
        identity_id = uuid4()
        target_id = uuid4()

        await logger.audit(
            conn,
            action="memory.write",
            result="success",
            tenant_id=tenant_id,
            identity_id=identity_id,
            client_id="client-1",
            target_id=target_id,
            target_kind="memory",
            ip_address="203.0.113.45",
            user_agent="claude-code/2.x",
            request_id="req-abc",
            details={"deduped": False, "embed_tokens": 142},
        )

        assert conn.execute.await_count == 1
        sql, *args = conn.execute.await_args.args
        assert "INSERT INTO audit_log" in sql
        # Verify the positional args in the order our SQL expects them
        assert args[0] == tenant_id  # tenant_id
        assert args[1] == "client-1"  # actor_client_id
        assert args[2] == identity_id  # actor_identity_id
        assert args[3] == "memory.write"  # action
        assert args[4] == target_id  # target_id
        assert args[5] == "memory"  # target_kind
        assert args[6] == "203.0.113.45"
        assert args[7] == "claude-code/2.x"
        assert args[8] == "req-abc"
        assert args[9] == "success"  # result
        assert args[10] is None  # error_code
        # details JSON-encoded
        assert json.loads(args[11]) == {"deduped": False, "embed_tokens": 142}

    @pytest.mark.asyncio
    async def test_minimal_call_no_optional_fields(self) -> None:
        conn = AsyncMock()
        logger = DbAuditLogger()
        await logger.audit(
            conn,
            action="auth.session_started",
            result="success",
            tenant_id=uuid4(),
        )
        assert conn.execute.await_count == 1
        _, *args = conn.execute.await_args.args
        # All optional args are None / empty
        assert args[1] is None  # client_id
        assert args[2] is None  # identity_id
        assert args[4] is None  # target_id
        assert args[5] is None  # target_kind
        assert args[10] is None  # error_code
        assert json.loads(args[11]) == {}

    @pytest.mark.asyncio
    async def test_denied_with_error_code(self) -> None:
        conn = AsyncMock()
        logger = DbAuditLogger()
        await logger.audit(
            conn,
            action="oauth.dcr_rejected",
            result="denied",
            tenant_id=None,
            error_code="unauthorized_client",
            details={"software_id": "rogue-tool"},
        )
        _, *args = conn.execute.await_args.args
        assert args[3] == "oauth.dcr_rejected"
        assert args[9] == "denied"
        assert args[10] == "unauthorized_client"

    @pytest.mark.asyncio
    async def test_db_failure_does_not_raise(self) -> None:
        conn = AsyncMock()
        conn.execute.side_effect = RuntimeError("db down")
        logger = DbAuditLogger()
        # Must NOT raise — audit failures are best-effort
        await logger.audit(
            conn,
            action="memory.write",
            result="success",
            tenant_id=uuid4(),
        )
        # And the call was attempted
        assert conn.execute.await_count == 1


# --------------------------------------------------------------------------
# NoopAuditLogger
# --------------------------------------------------------------------------


class TestNoopAuditLogger:
    @pytest.mark.asyncio
    async def test_drops_silently(self) -> None:
        conn = AsyncMock()
        logger = NoopAuditLogger()
        await logger.audit(
            conn,
            action="memory.write",
            result="success",
            tenant_id=uuid4(),
            details={"x": 1},
        )
        # No DB call attempted
        assert conn.execute.await_count == 0
