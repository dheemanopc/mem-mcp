"""Unit tests for signup request flow."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from mem_mcp.web.signup_requests.public_routes import SubmitInput


class TestSubmitInput:
    """Test SubmitInput model validation."""

    def test_valid_submit_input(self) -> None:
        """Test creating a valid submit input."""
        payload = SubmitInput(
            email="user@example.com",
            name="John Doe",
            reason="I want to use mem-mcp",
            client_intent=["claude_code", "claude_chat"],
        )
        assert payload.email == "user@example.com"
        assert payload.name == "John Doe"
        assert payload.website is None

    def test_honeypot_field_present(self) -> None:
        """Test that honeypot field can be set."""
        payload = SubmitInput(
            email="user@example.com",
            website="http://example.com",
        )
        assert payload.website == "http://example.com"

    def test_name_max_length_validation(self) -> None:
        """Test name field max length validation."""
        long_name = "a" * 201
        with pytest.raises(ValueError):
            SubmitInput(email="user@example.com", name=long_name)

    def test_reason_max_length_validation(self) -> None:
        """Test reason field max length validation."""
        long_reason = "a" * 1001
        with pytest.raises(ValueError):
            SubmitInput(email="user@example.com", reason=long_reason)


@pytest.mark.asyncio
class TestUpsertRequest:
    """Test repository.upsert_request function."""

    async def test_upsert_request_returns_token(self) -> None:
        """Test that upsert_request returns a valid token."""
        from mem_mcp.web.signup_requests.repository import upsert_request

        mock_conn = AsyncMock()
        mock_row = {
            "id": uuid4(),
            "email": "user@example.com",
            "name": "John",
            "reason": "Testing",
            "client_intent": ["test"],
            "status": "awaiting_verification",
            "submitted_at": None,
            "reviewed_at": None,
            "reviewed_by": None,
            "review_notes": None,
            "email_verified_at": None,
            "verify_expires_at": None,
        }
        mock_conn.fetchrow = AsyncMock(side_effect=[None, mock_row])

        @asynccontextmanager
        async def mock_acquire():  # type: ignore[no-untyped-def]
            yield mock_conn

        pool = AsyncMock()
        pool.acquire = mock_acquire

        req, token = await upsert_request(
            pool,
            email="user@example.com",
            name="John",
            reason="Testing",
            client_intent=["test"],
            ip_address="127.0.0.1",
            user_agent="test-agent",
        )

        assert isinstance(token, str)
        assert len(token) > 20
        assert req.email == "user@example.com"

    async def test_upsert_request_approved_raises(self) -> None:
        """Test that upsert_request raises ValueError for already-approved requests."""
        from mem_mcp.web.signup_requests.repository import upsert_request

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"status": "approved"})

        @asynccontextmanager
        async def mock_acquire():  # type: ignore[no-untyped-def]
            yield mock_conn

        pool = AsyncMock()
        pool.acquire = mock_acquire

        with pytest.raises(ValueError, match="already approved"):
            await upsert_request(
                pool,
                email="user@example.com",
                name="John",
                reason="Testing",
                client_intent=[],
                ip_address=None,
                user_agent=None,
            )

    async def test_upsert_request_rejected_raises(self) -> None:
        """Test that upsert_request raises ValueError for already-rejected requests."""
        from mem_mcp.web.signup_requests.repository import upsert_request

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"status": "rejected"})

        @asynccontextmanager
        async def mock_acquire():  # type: ignore[no-untyped-def]
            yield mock_conn

        pool = AsyncMock()
        pool.acquire = mock_acquire

        with pytest.raises(ValueError, match="already rejected"):
            await upsert_request(
                pool,
                email="user@example.com",
                name="John",
                reason="Testing",
                client_intent=[],
                ip_address=None,
                user_agent=None,
            )


@pytest.mark.asyncio
class TestRateLimit:
    """Test rate_limit.check_and_increment function."""

    async def test_rate_limit_under_limit(self) -> None:
        """Test that check_and_increment returns True when under limit."""
        from mem_mcp.web.signup_requests.rate_limit import check_and_increment

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"count": 1})

        @asynccontextmanager
        async def mock_acquire():  # type: ignore[no-untyped-def]
            yield mock_conn

        pool = AsyncMock()
        pool.acquire = mock_acquire

        result = await check_and_increment(
            pool,
            key="test:key",
            bucket_seconds=3600,
            limit=10,
        )

        assert result is True

    async def test_rate_limit_over_limit(self) -> None:
        """Test that check_and_increment returns False when over limit."""
        from mem_mcp.web.signup_requests.rate_limit import check_and_increment

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"count": 11})

        @asynccontextmanager
        async def mock_acquire():  # type: ignore[no-untyped-def]
            yield mock_conn

        pool = AsyncMock()
        pool.acquire = mock_acquire

        result = await check_and_increment(
            pool,
            key="test:key",
            bucket_seconds=3600,
            limit=10,
        )

        assert result is False

    async def test_rate_limit_at_limit(self) -> None:
        """Test that check_and_increment returns True when at limit."""
        from mem_mcp.web.signup_requests.rate_limit import check_and_increment

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"count": 10})

        @asynccontextmanager
        async def mock_acquire():  # type: ignore[no-untyped-def]
            yield mock_conn

        pool = AsyncMock()
        pool.acquire = mock_acquire

        result = await check_and_increment(
            pool,
            key="test:key",
            bucket_seconds=3600,
            limit=10,
        )

        assert result is True


@pytest.mark.asyncio
class TestRequireOperator:
    """Test admin_routes._require_operator function."""

    async def test_require_operator_valid_session(self) -> None:
        """Test that _require_operator returns email when session is valid."""
        from mem_mcp.web.signup_requests.admin_routes import _require_operator

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"email": "anand@dheemantech.com"})

        @asynccontextmanager
        async def mock_system_tx(pool):  # type: ignore[no-untyped-def]
            yield mock_conn

        # Mock lookup_session
        mock_session = MagicMock()
        mock_session.identity_id = uuid4()

        with patch(
            "mem_mcp.web.signup_requests.admin_routes.lookup_session",
            new_callable=AsyncMock,
            return_value=mock_session,
        ):
            with patch("mem_mcp.web.signup_requests.admin_routes.system_tx", mock_system_tx):
                with patch(
                    "mem_mcp.web.signup_requests.admin_routes.get_settings"
                ) as mock_settings:
                    mock_settings.return_value.operator_email = "anand@dheemantech.com"
                    pool = AsyncMock()
                    result = await _require_operator(pool, "valid_session_token")

        assert result == "anand@dheemantech.com"

    async def test_require_operator_no_session(self) -> None:
        """Test that _require_operator raises 401 when no session provided."""
        from mem_mcp.web.signup_requests.admin_routes import _require_operator

        pool = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await _require_operator(pool, None)

        assert exc_info.value.status_code == 401

    async def test_require_operator_invalid_session(self) -> None:
        """Test that _require_operator raises 401 when session is invalid."""
        from mem_mcp.web.signup_requests.admin_routes import _require_operator

        pool = AsyncMock()

        with patch(
            "mem_mcp.web.signup_requests.admin_routes.lookup_session",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await _require_operator(pool, "invalid_session")

        assert exc_info.value.status_code == 401

    async def test_require_operator_email_mismatch(self) -> None:
        """Test that _require_operator raises 403 when email doesn't match operator."""
        from mem_mcp.web.signup_requests.admin_routes import _require_operator

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"email": "user@example.com"})

        @asynccontextmanager
        async def mock_system_tx(pool):  # type: ignore[no-untyped-def]
            yield mock_conn

        # Mock lookup_session
        mock_session = MagicMock()
        mock_session.identity_id = uuid4()

        with patch(
            "mem_mcp.web.signup_requests.admin_routes.lookup_session",
            new_callable=AsyncMock,
            return_value=mock_session,
        ):
            with patch("mem_mcp.web.signup_requests.admin_routes.system_tx", mock_system_tx):
                with patch(
                    "mem_mcp.web.signup_requests.admin_routes.get_settings"
                ) as mock_settings:
                    mock_settings.return_value.operator_email = "anand@dheemantech.com"
                    pool = AsyncMock()
                    with pytest.raises(HTTPException) as exc_info:
                        await _require_operator(pool, "valid_but_wrong_user")

        assert exc_info.value.status_code == 403

    async def test_require_operator_no_email(self) -> None:
        """Test that _require_operator raises 403 when email not found on identity."""
        from mem_mcp.web.signup_requests.admin_routes import _require_operator

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"email": None})

        @asynccontextmanager
        async def mock_system_tx(pool):  # type: ignore[no-untyped-def]
            yield mock_conn

        # Mock lookup_session
        mock_session = MagicMock()
        mock_session.identity_id = uuid4()

        with patch(
            "mem_mcp.web.signup_requests.admin_routes.lookup_session",
            new_callable=AsyncMock,
            return_value=mock_session,
        ):
            with patch("mem_mcp.web.signup_requests.admin_routes.system_tx", mock_system_tx):
                pool = AsyncMock()
                with pytest.raises(HTTPException) as exc_info:
                    await _require_operator(pool, "valid_but_no_email")

        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
class TestMarkReviewed:
    """Test repository.mark_reviewed function."""

    async def test_mark_reviewed_pending_request(self) -> None:
        """Test marking a pending request as approved."""
        from mem_mcp.web.signup_requests.repository import mark_reviewed

        request_id = uuid4()
        mock_row = {
            "id": request_id,
            "email": "user@example.com",
            "name": "John",
            "reason": "Testing",
            "client_intent": [],
            "status": "approved",
            "submitted_at": None,
            "reviewed_at": None,
            "reviewed_by": "anand@dheemantech.com",
            "review_notes": "Approved",
            "email_verified_at": None,
            "verify_expires_at": None,
        }

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=mock_row)

        @asynccontextmanager
        async def mock_acquire():  # type: ignore[no-untyped-def]
            yield mock_conn

        pool = AsyncMock()
        pool.acquire = mock_acquire

        result = await mark_reviewed(
            pool,
            request_id=request_id,
            status="approved",
            reviewer="anand@dheemantech.com",
            notes="Approved",
        )

        assert result is not None
        assert result.id == request_id

    async def test_mark_reviewed_not_pending_returns_none(self) -> None:
        """Test that mark_reviewed returns None if request is not in pending state."""
        from mem_mcp.web.signup_requests.repository import mark_reviewed

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)

        @asynccontextmanager
        async def mock_acquire():  # type: ignore[no-untyped-def]
            yield mock_conn

        pool = AsyncMock()
        pool.acquire = mock_acquire

        request_id = uuid4()
        result = await mark_reviewed(
            pool,
            request_id=request_id,
            status="approved",
            reviewer="anand@dheemantech.com",
            notes=None,
        )

        assert result is None
