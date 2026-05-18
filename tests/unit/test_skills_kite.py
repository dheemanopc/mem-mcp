"""Unit tests for the Kite skill (auth + client + skill dispatch). Mock-based via httpx.MockTransport."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

import httpx
import pytest

# ==========================================================================
# Helpers
# ==========================================================================


def _make_mock_client(handler: Any) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient backed by a MockTransport."""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


def _build_skill_ctx(creds: dict[str, Any] | None = None) -> Any:
    from mem_mcp.skills.base import SkillCallContext

    return SkillCallContext(
        tenant_id=uuid4(),
        identity_id=uuid4(),
        client_id="test-client",
        request_id=str(uuid4()),
        credentials=creds
        if creds is not None
        else {
            "api_key": "AKEY",
            "api_secret": "ASEC",
            "access_token": "ATOK",
        },
        tool_call_id=uuid4(),
    )


# ==========================================================================
# Auth
# ==========================================================================


class TestKiteAuth:
    def test_checksum_known_value(self) -> None:
        from mem_mcp.skills.kite.auth import compute_checksum

        actual = compute_checksum("AKEY", "RTOK", "ASEC")
        expected = hashlib.sha256(b"AKEYRTOKASEC").hexdigest()
        assert actual == expected

    @pytest.mark.asyncio
    async def test_exchange_request_token_success(self) -> None:
        from mem_mcp.skills.kite.auth import exchange_request_token

        def handler(req: httpx.Request) -> httpx.Response:
            assert req.method == "POST"
            assert req.url.path == "/session/token"
            assert b"api_key=AKEY" in req.content
            assert b"request_token=RTOK" in req.content
            assert b"checksum=" in req.content
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "access_token": "NEW_ATOK",
                        "public_token": "PTOK",
                        "user_id": "U1",
                    },
                },
            )

        async with _make_mock_client(handler) as client:
            data = await exchange_request_token("AKEY", "RTOK", "ASEC", http=client)
        assert data["access_token"] == "NEW_ATOK"
        assert data["user_id"] == "U1"

    @pytest.mark.asyncio
    async def test_exchange_request_token_error(self) -> None:
        from mem_mcp.skills.kite.auth import KiteAuthError, exchange_request_token

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={
                    "status": "error",
                    "message": "Invalid request_token",
                    "error_type": "TokenException",
                },
            )

        async with _make_mock_client(handler) as client:
            with pytest.raises(KiteAuthError) as exc:
                await exchange_request_token("AKEY", "RTOK", "ASEC", http=client)
        assert "Invalid request_token" in str(exc.value)
        assert exc.value.error_type == "TokenException"

    @pytest.mark.asyncio
    async def test_exchange_request_token_non_json(self) -> None:
        from mem_mcp.skills.kite.auth import KiteAuthError, exchange_request_token

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(500, content=b"<html>nginx 500</html>")

        async with _make_mock_client(handler) as client:
            with pytest.raises(KiteAuthError):
                await exchange_request_token("AKEY", "RTOK", "ASEC", http=client)


# ==========================================================================
# Client
# ==========================================================================


def _envelope(data: Any) -> dict[str, Any]:
    return {"status": "success", "data": data}


def _error_envelope(message: str, error_type: str = "GeneralException") -> dict[str, Any]:
    return {"status": "error", "message": message, "error_type": error_type}


class TestKiteClient:
    @pytest.mark.asyncio
    async def test_auth_header_shape(self) -> None:
        from mem_mcp.skills.kite.client import KiteClient

        captured: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["auth"] = req.headers.get("Authorization")
            captured["kite_version"] = req.headers.get("X-Kite-Version")
            return httpx.Response(200, json=_envelope([]))

        async with _make_mock_client(handler) as http:
            c = KiteClient(http=http)
            await c.get_holdings(api_key="AKEY", access_token="ATOK")
        assert captured["auth"] == "token AKEY:ATOK"
        assert captured["kite_version"] == "3"

    @pytest.mark.asyncio
    async def test_get_holdings_returns_data(self) -> None:
        from mem_mcp.skills.kite.client import KiteClient

        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.path == "/portfolio/holdings"
            return httpx.Response(
                200, json=_envelope([{"tradingsymbol": "RELIANCE", "quantity": 10}])
            )

        async with _make_mock_client(handler) as http:
            c = KiteClient(http=http)
            data = await c.get_holdings(api_key="AKEY", access_token="ATOK")
        assert data == [{"tradingsymbol": "RELIANCE", "quantity": 10}]

    @pytest.mark.asyncio
    async def test_get_positions_returns_net_and_day(self) -> None:
        from mem_mcp.skills.kite.client import KiteClient

        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.path == "/portfolio/positions"
            return httpx.Response(200, json=_envelope({"net": [{"x": 1}], "day": [{"y": 2}]}))

        async with _make_mock_client(handler) as http:
            c = KiteClient(http=http)
            data = await c.get_positions(api_key="AKEY", access_token="ATOK")
        assert data == {"net": [{"x": 1}], "day": [{"y": 2}]}

    @pytest.mark.asyncio
    async def test_get_margins_with_segment(self) -> None:
        from mem_mcp.skills.kite.client import KiteClient

        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.path == "/user/margins/equity"
            return httpx.Response(200, json=_envelope({"available": {"cash": 50000}}))

        async with _make_mock_client(handler) as http:
            c = KiteClient(http=http)
            data = await c.get_margins(api_key="AKEY", access_token="ATOK", segment="equity")
        assert data["available"]["cash"] == 50000

    @pytest.mark.asyncio
    async def test_get_margins_without_segment(self) -> None:
        from mem_mcp.skills.kite.client import KiteClient

        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.path == "/user/margins"
            return httpx.Response(200, json=_envelope({"equity": {}, "commodity": {}}))

        async with _make_mock_client(handler) as http:
            c = KiteClient(http=http)
            data = await c.get_margins(api_key="AKEY", access_token="ATOK")
        assert "equity" in data and "commodity" in data

    @pytest.mark.asyncio
    async def test_get_quote_passes_multiple_instruments(self) -> None:
        from mem_mcp.skills.kite.client import KiteClient

        captured: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["url"] = str(req.url)
            return httpx.Response(200, json=_envelope({"NSE:RECLTD": {"last_price": 290.0}}))

        async with _make_mock_client(handler) as http:
            c = KiteClient(http=http)
            data = await c.get_quote(
                api_key="AKEY",
                access_token="ATOK",
                instruments=["NSE:RECLTD", "NSE:POWERGRID"],
            )
        # Both instruments must appear as i= query params
        assert "i=NSE%3ARECLTD" in captured["url"] or "i=NSE:RECLTD" in captured["url"]
        assert "i=NSE%3APOWERGRID" in captured["url"] or "i=NSE:POWERGRID" in captured["url"]
        assert "NSE:RECLTD" in data

    @pytest.mark.asyncio
    async def test_get_historical_data_path_and_params(self) -> None:
        from mem_mcp.skills.kite.client import KiteClient

        captured: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["path"] = req.url.path
            captured["url"] = str(req.url)
            return httpx.Response(
                200, json=_envelope({"candles": [["2026-05-17", 100, 110, 95, 108, 1000]]})
            )

        async with _make_mock_client(handler) as http:
            c = KiteClient(http=http)
            data = await c.get_historical_data(
                api_key="AKEY",
                access_token="ATOK",
                instrument_token=12345,
                interval="day",
                from_date="2026-05-01",
                to_date="2026-05-17",
                continuous=True,
                oi=True,
            )
        assert "12345" in captured["path"]
        assert "/day" in captured["path"]
        assert "continuous=1" in captured["url"]
        assert "oi=1" in captured["url"]
        assert len(data["candles"]) == 1

    @pytest.mark.asyncio
    async def test_place_order_posts_form_data(self) -> None:
        from mem_mcp.skills.kite.client import KiteClient

        captured: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["method"] = req.method
            captured["path"] = req.url.path
            captured["body"] = req.content.decode()
            return httpx.Response(200, json=_envelope({"order_id": "ORDER123"}))

        async with _make_mock_client(handler) as http:
            c = KiteClient(http=http)
            data = await c.place_order(
                api_key="AKEY",
                access_token="ATOK",
                variety="regular",
                order_params={
                    "tradingsymbol": "RELIANCE",
                    "quantity": 10,
                    "transaction_type": "BUY",
                },
            )
        assert captured["method"] == "POST"
        assert captured["path"] == "/orders/regular"
        assert "tradingsymbol=RELIANCE" in captured["body"]
        assert data == {"order_id": "ORDER123"}

    @pytest.mark.asyncio
    async def test_cancel_order_delete(self) -> None:
        from mem_mcp.skills.kite.client import KiteClient

        captured: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["method"] = req.method
            captured["path"] = req.url.path
            return httpx.Response(200, json=_envelope({"order_id": "ORDER123"}))

        async with _make_mock_client(handler) as http:
            c = KiteClient(http=http)
            data = await c.cancel_order(
                api_key="AKEY",
                access_token="ATOK",
                variety="regular",
                order_id="ORDER123",
            )
        assert captured["method"] == "DELETE"
        assert captured["path"] == "/orders/regular/ORDER123"
        assert data == {"order_id": "ORDER123"}

    @pytest.mark.asyncio
    async def test_kite_api_error_on_non_200(self) -> None:
        from mem_mcp.skills.kite.client import KiteApiError, KiteClient

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401, json=_error_envelope("Invalid api_key or access_token", "TokenException")
            )

        async with _make_mock_client(handler) as http:
            c = KiteClient(http=http)
            with pytest.raises(KiteApiError) as exc:
                await c.get_holdings(api_key="AKEY", access_token="ATOK")
        assert exc.value.status_code == 401
        assert exc.value.error_type == "TokenException"
        assert "Invalid api_key" in str(exc.value)

    @pytest.mark.asyncio
    async def test_kite_api_error_on_status_error_200(self) -> None:
        from mem_mcp.skills.kite.client import KiteApiError, KiteClient

        def handler(req: httpx.Request) -> httpx.Response:
            # Kite occasionally returns 200 with status=error
            return httpx.Response(200, json=_error_envelope("Invalid quantity"))

        async with _make_mock_client(handler) as http:
            c = KiteClient(http=http)
            with pytest.raises(KiteApiError) as exc:
                await c.place_order(
                    api_key="AKEY",
                    access_token="ATOK",
                    variety="regular",
                    order_params={"x": 1},
                )
        assert "Invalid quantity" in str(exc.value)


# ==========================================================================
# KiteSkill
# ==========================================================================


class TestKiteSkill:
    def test_required_credentials_three_fields(self) -> None:
        from mem_mcp.skills.kite.skill import KiteSkill

        skill = KiteSkill()
        names = {c.name for c in skill.required_credentials()}
        assert names == {"api_key", "api_secret", "access_token"}

    def test_list_tools_count_and_names(self) -> None:
        from mem_mcp.skills.kite.skill import KiteSkill

        skill = KiteSkill()
        tools = skill.list_tools()
        assert len(tools) == 8
        names = {t.name for t in tools}
        assert names == {
            "get_holdings",
            "get_positions",
            "get_margins",
            "get_quote",
            "get_historical_data",
            "get_orders",
            "place_order",
            "cancel_order",
        }

    def test_list_tools_read_only_split(self) -> None:
        from mem_mcp.skills.kite.skill import KiteSkill

        skill = KiteSkill()
        tools = {t.name: t for t in skill.list_tools()}
        assert tools["get_holdings"].read_only is True
        assert tools["get_quote"].read_only is True
        assert tools["place_order"].read_only is False
        assert tools["cancel_order"].read_only is False

    @pytest.mark.asyncio
    async def test_call_tool_get_holdings_routes_through_client(self) -> None:
        from mem_mcp.skills.kite.client import KiteClient
        from mem_mcp.skills.kite.skill import (
            GetHoldingsOutput,
            KiteSkill,
            _EmptyInput,
        )

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"status": "success", "data": [{"tradingsymbol": "RELIANCE"}]}
            )

        async with _make_mock_client(handler) as http:
            client = KiteClient(http=http)
            skill = KiteSkill(client=client)
            out = await skill.call_tool("get_holdings", _EmptyInput(), _build_skill_ctx())
        assert isinstance(out, GetHoldingsOutput)
        assert out.holdings == [{"tradingsymbol": "RELIANCE"}]

    @pytest.mark.asyncio
    async def test_call_tool_place_order_returns_order_id(self) -> None:
        from mem_mcp.skills.kite.client import KiteClient
        from mem_mcp.skills.kite.skill import (
            KiteSkill,
            PlaceOrderInput,
            PlaceOrderOutput,
        )

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "success", "data": {"order_id": "ORDER42"}})

        async with _make_mock_client(handler) as http:
            client = KiteClient(http=http)
            skill = KiteSkill(client=client)
            inp = PlaceOrderInput(
                variety="regular",
                tradingsymbol="RELIANCE",
                exchange="NSE",
                transaction_type="BUY",
                quantity=10,
                product="MIS",
                order_type="LIMIT",
                price=2500.0,
            )
            ctx = _build_skill_ctx(
                {
                    "api_key": "AKEY",
                    "api_secret": "ASEC",
                    "access_token": "ATOK",
                    "orders_enabled": True,
                }
            )
            out = await skill.call_tool("place_order", inp, ctx)
        assert isinstance(out, PlaceOrderOutput)
        assert out.order_id == "ORDER42"

    @pytest.mark.asyncio
    async def test_call_tool_missing_credentials_raises(self) -> None:
        from mem_mcp.skills.base import SkillError
        from mem_mcp.skills.kite.skill import KiteSkill, _EmptyInput

        skill = KiteSkill()
        # missing access_token
        ctx = _build_skill_ctx(creds={"api_key": "AKEY", "api_secret": "ASEC"})
        with pytest.raises(SkillError):
            await skill.call_tool("get_holdings", _EmptyInput(), ctx)

    @pytest.mark.asyncio
    async def test_call_tool_unknown_tool_raises(self) -> None:
        from mem_mcp.skills.base import SkillToolNotFoundError
        from mem_mcp.skills.kite.skill import KiteSkill, _EmptyInput

        skill = KiteSkill()
        with pytest.raises(SkillToolNotFoundError):
            await skill.call_tool("not_a_real_tool", _EmptyInput(), _build_skill_ctx())

    @pytest.mark.asyncio
    async def test_call_tool_kite_error_wrapped_as_skill_error(self) -> None:
        from mem_mcp.skills.base import SkillError
        from mem_mcp.skills.kite.client import KiteClient
        from mem_mcp.skills.kite.skill import KiteSkill, _EmptyInput

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={
                    "status": "error",
                    "message": "Invalid token",
                    "error_type": "GeneralException",
                },
            )

        async with _make_mock_client(handler) as http:
            client = KiteClient(http=http)
            skill = KiteSkill(client=client)
            with pytest.raises(SkillError) as exc:
                await skill.call_tool("get_holdings", _EmptyInput(), _build_skill_ctx())
        assert "kite_get_holdings" in str(exc.value)

    @pytest.mark.asyncio
    async def test_call_tool_place_order_gated_when_orders_disabled(self) -> None:
        from mem_mcp.skills.base import SkillError
        from mem_mcp.skills.kite.client import KiteClient
        from mem_mcp.skills.kite.skill import KiteSkill, PlaceOrderInput

        def handler(req: httpx.Request) -> httpx.Response:
            raise AssertionError("should not reach Kite if gated")

        async with _make_mock_client(handler) as http:
            client = KiteClient(http=http)
            skill = KiteSkill(client=client)
            ctx = _build_skill_ctx(
                {
                    "api_key": "AKEY",
                    "api_secret": "ASEC",
                    "access_token": "ATOK",
                    "orders_enabled": False,
                }
            )
            inp = PlaceOrderInput(
                variety="regular",
                tradingsymbol="RELIANCE",
                exchange="NSE",
                transaction_type="BUY",
                quantity=10,
                product="MIS",
                order_type="MARKET",
            )
            with pytest.raises(SkillError) as exc:
                await skill.call_tool("place_order", inp, ctx)
        assert "trading is not enabled" in str(exc.value)

    @pytest.mark.asyncio
    async def test_call_tool_place_order_allowed_when_orders_enabled(self) -> None:
        from mem_mcp.skills.kite.client import KiteClient
        from mem_mcp.skills.kite.skill import KiteSkill, PlaceOrderInput

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_envelope({"order_id": "ORD123"}))

        async with _make_mock_client(handler) as http:
            client = KiteClient(http=http)
            skill = KiteSkill(client=client)
            ctx = _build_skill_ctx(
                {
                    "api_key": "AKEY",
                    "api_secret": "ASEC",
                    "access_token": "ATOK",
                    "orders_enabled": True,
                }
            )
            inp = PlaceOrderInput(
                variety="regular",
                tradingsymbol="RELIANCE",
                exchange="NSE",
                transaction_type="BUY",
                quantity=10,
                product="MIS",
                order_type="MARKET",
            )
            out = await skill.call_tool("place_order", inp, ctx)
        from mem_mcp.skills.kite.skill import PlaceOrderOutput

        assert isinstance(out, PlaceOrderOutput)
        assert out.order_id == "ORD123"

    @pytest.mark.asyncio
    async def test_call_tool_cancel_order_gated_when_orders_disabled(self) -> None:
        from mem_mcp.skills.base import SkillError
        from mem_mcp.skills.kite.client import KiteClient
        from mem_mcp.skills.kite.skill import CancelOrderInput, KiteSkill

        def handler(req: httpx.Request) -> httpx.Response:
            raise AssertionError("should not reach Kite if gated")

        async with _make_mock_client(handler) as http:
            client = KiteClient(http=http)
            skill = KiteSkill(client=client)
            ctx = _build_skill_ctx(
                {
                    "api_key": "AKEY",
                    "api_secret": "ASEC",
                    "access_token": "ATOK",
                    "orders_enabled": False,
                }
            )
            inp = CancelOrderInput(variety="regular", order_id="ORD123")
            with pytest.raises(SkillError) as exc:
                await skill.call_tool("cancel_order", inp, ctx)
        assert "trading is not enabled" in str(exc.value)


# ==========================================================================
# Auto-Login (Mode C)
# ==========================================================================


class TestKiteAutoLogin:
    @pytest.mark.asyncio
    async def test_login_with_credentials_happy_path(self) -> None:
        from unittest.mock import AsyncMock, patch

        from mem_mcp.skills.kite.auth import login_with_credentials

        call_count = {"n": 0}

        async def mock_get(*args: Any, **kwargs: Any) -> Any:
            # Step 4: return response with request_token in URL
            return AsyncMock(url="https://example.com?request_token=RTOK123")

        def handler(req: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Step 1: login
                assert req.url.path == "/api/login"
                return httpx.Response(
                    200, json={"status": "success", "data": {"request_id": "REQ123"}}
                )
            elif call_count["n"] == 2:
                # Step 3: 2FA
                assert req.url.path == "/api/twofa"
                return httpx.Response(200, json={"status": "success", "data": {}})
            elif call_count["n"] == 3:
                # Step 5: exchange for access_token
                assert req.url.path == "/session/token"
                return httpx.Response(
                    200,
                    json={
                        "status": "success",
                        "data": {
                            "access_token": "ATOK123",
                            "public_token": "PTOK",
                            "user_id": "U1",
                        },
                    },
                )
            raise AssertionError(f"unexpected call #{call_count['n']}")

        # Mock the GET call (step 4) — returns a 302 with Location header
        # carrying request_token. We don't follow the redirect server-side.
        mock_resp = AsyncMock()
        mock_resp.status_code = 302
        mock_resp.headers = {
            "location": "https://tradeapi.dheemantech.in/kite-callback?request_token=RTOK123&action=login&status=success"
        }
        with patch(
            "mem_mcp.skills.kite.auth.httpx.AsyncClient.get",
            new_callable=AsyncMock,
        ) as mock_get_call:
            mock_get_call.return_value = mock_resp
            async with _make_mock_client(handler) as http:
                result = await login_with_credentials(
                    api_key="AKEY",
                    api_secret="ASEC",
                    user_id="U1",
                    password="pwd123",
                    totp_secret="JBSWY3DPEBLW64TMMQ======",
                    http=http,
                )
            # Critical: verify we did NOT follow the redirect (the original
            # bug — algotrade memory 9441787e). The kwarg should be False.
            assert mock_get_call.call_args.kwargs["follow_redirects"] is False
        assert result["access_token"] == "ATOK123"
        assert result["kite_user_id"] == "U1"
        assert result["public_token"] == "PTOK"

    @pytest.mark.asyncio
    async def test_login_with_credentials_step1_failure(self) -> None:
        from mem_mcp.skills.kite.auth import KiteCredentialsError, login_with_credentials

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/api/login":
                return httpx.Response(
                    400,
                    json={
                        "status": "error",
                        "message": "Invalid user_id or password",
                        "error_type": "InvalidCredentials",
                    },
                )
            raise AssertionError(f"unexpected request to {req.url.path}")

        async with _make_mock_client(handler) as http:
            with pytest.raises(KiteCredentialsError) as exc:
                await login_with_credentials(
                    api_key="AKEY",
                    api_secret="ASEC",
                    user_id="baduser",
                    password="badpwd",
                    totp_secret="JBSWY3DPEBLW64TMMQ======",
                    http=http,
                )
        assert "Invalid user_id or password" in str(exc.value)

    @pytest.mark.asyncio
    async def test_login_with_credentials_invalid_totp_secret(self) -> None:
        from mem_mcp.skills.kite.auth import KiteCredentialsError, login_with_credentials

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/api/login":
                return httpx.Response(
                    200, json={"status": "success", "data": {"request_id": "REQ123"}}
                )
            raise AssertionError(f"unexpected request to {req.url.path}")

        async with _make_mock_client(handler) as http:
            with pytest.raises(KiteCredentialsError) as exc:
                await login_with_credentials(
                    api_key="AKEY",
                    api_secret="ASEC",
                    user_id="U1",
                    password="pwd123",
                    totp_secret="INVALID_BASE32_SECRET!!!",
                    http=http,
                )
        assert "invalid TOTP secret" in str(exc.value)
        # Observability: step annotation present so callers know WHERE it failed
        assert exc.value.step == "totp_generate"

    @pytest.mark.asyncio
    async def test_login_step1_failure_carries_step_and_status(self) -> None:
        """Step-1 KiteCredentialsError must carry step='api_login' + upstream_status."""
        from mem_mcp.skills.kite.auth import KiteCredentialsError, login_with_credentials

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/api/login":
                return httpx.Response(
                    403,
                    json={
                        "status": "error",
                        "message": "Account locked",
                        "error_type": "UserBlocked",
                    },
                )
            raise AssertionError(f"unexpected request to {req.url.path}")

        async with _make_mock_client(handler) as http:
            with pytest.raises(KiteCredentialsError) as exc:
                await login_with_credentials(
                    api_key="AKEY",
                    api_secret="ASEC",
                    user_id="U1",
                    password="pwd123",
                    totp_secret="JBSWY3DPEBLW64TMMQ======",
                    http=http,
                )
        assert exc.value.step == "api_login"
        assert exc.value.upstream_status == 403
        assert exc.value.error_type == "UserBlocked"

    @pytest.mark.asyncio
    async def test_login_step4_connect_login_transport_error(self) -> None:
        """If httpx raises RequestError on /connect/login itself (rare — only
        if kite.zerodha.com is unreachable), surface as KiteCredentialsError
        with step='connect_login'. We no longer follow the redirect (per memsys
        9441787e), so transport errors are only possible on the direct request."""
        from unittest.mock import AsyncMock, patch

        from mem_mcp.skills.kite.auth import KiteCredentialsError, login_with_credentials

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/api/login":
                return httpx.Response(
                    200, json={"status": "success", "data": {"request_id": "REQ123"}}
                )
            if req.url.path == "/api/twofa":
                return httpx.Response(200, json={"status": "success", "data": {}})
            raise AssertionError(f"unexpected request to {req.url.path}")

        async with _make_mock_client(handler) as http:
            with patch.object(
                http,
                "get",
                new=AsyncMock(side_effect=httpx.ConnectError("Cannot connect to host")),
            ):
                with pytest.raises(KiteCredentialsError) as exc:
                    await login_with_credentials(
                        api_key="AKEY",
                        api_secret="ASEC",
                        user_id="U1",
                        password="pwd123",
                        totp_secret="JBSWY3DPEBLW64TMMQ======",
                        http=http,
                    )
        assert exc.value.step == "connect_login"
        assert "ConnectError" in str(exc.value)

    @pytest.mark.asyncio
    async def test_login_step4_non_302_response(self) -> None:
        """If /connect/login returns anything other than 302 (e.g., 200 with
        an error page), surface as KiteCredentialsError with step='connect_login'
        and upstream_status=<actual code>."""
        from unittest.mock import AsyncMock, patch

        from mem_mcp.skills.kite.auth import KiteCredentialsError, login_with_credentials

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/api/login":
                return httpx.Response(
                    200, json={"status": "success", "data": {"request_id": "REQ123"}}
                )
            if req.url.path == "/api/twofa":
                return httpx.Response(200, json={"status": "success", "data": {}})
            raise AssertionError(f"unexpected request to {req.url.path}")

        mock_resp = AsyncMock()
        mock_resp.status_code = 500
        mock_resp.headers = {}

        async with _make_mock_client(handler) as http:
            with patch.object(http, "get", new=AsyncMock(return_value=mock_resp)):
                with pytest.raises(KiteCredentialsError) as exc:
                    await login_with_credentials(
                        api_key="AKEY",
                        api_secret="ASEC",
                        user_id="U1",
                        password="pwd123",
                        totp_secret="JBSWY3DPEBLW64TMMQ======",
                        http=http,
                    )
        assert exc.value.step == "connect_login"
        assert exc.value.upstream_status == 500
        assert "expected 302" in str(exc.value)

    @pytest.mark.asyncio
    async def test_login_step4_302_without_request_token(self) -> None:
        """If /connect/login returns 302 but the Location header has no
        request_token param (auth flow incomplete on Zerodha's end), surface as
        KiteCredentialsError carrying the offending Location URL."""
        from unittest.mock import AsyncMock, patch

        from mem_mcp.skills.kite.auth import KiteCredentialsError, login_with_credentials

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/api/login":
                return httpx.Response(
                    200, json={"status": "success", "data": {"request_id": "REQ123"}}
                )
            if req.url.path == "/api/twofa":
                return httpx.Response(200, json={"status": "success", "data": {}})
            raise AssertionError(f"unexpected request to {req.url.path}")

        mock_resp = AsyncMock()
        mock_resp.status_code = 302
        mock_resp.headers = {
            "location": "https://kite.zerodha.com/connect/login?api_key=X&status=error"
        }

        async with _make_mock_client(handler) as http:
            with patch.object(http, "get", new=AsyncMock(return_value=mock_resp)):
                with pytest.raises(KiteCredentialsError) as exc:
                    await login_with_credentials(
                        api_key="AKEY",
                        api_secret="ASEC",
                        user_id="U1",
                        password="pwd123",
                        totp_secret="JBSWY3DPEBLW64TMMQ======",
                        http=http,
                    )
        assert exc.value.step == "connect_login"
        assert "missing request_token" in str(exc.value)

    @pytest.mark.asyncio
    async def test_call_tool_token_expired_triggers_relogin(self) -> None:
        from unittest.mock import AsyncMock, patch

        from mem_mcp.skills.kite.client import KiteClient
        from mem_mcp.skills.kite.skill import GetHoldingsOutput, KiteSkill, _EmptyInput

        call_count = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First call fails with TokenException
                return httpx.Response(
                    401,
                    json={
                        "status": "error",
                        "message": "Invalid token",
                        "error_type": "TokenException",
                    },
                )
            elif call_count["n"] == 2:
                # After re-login (in login_with_credentials step 5), retry succeeds
                return httpx.Response(
                    200, json={"status": "success", "data": [{"tradingsymbol": "RELIANCE"}]}
                )
            raise AssertionError(f"unexpected call #{call_count['n']}")

        persist_called: dict[str, Any] = {}

        async def mock_persist(creds: dict[str, Any]) -> None:
            persist_called["creds"] = creds

        async with _make_mock_client(handler) as http:
            # Mock login_with_credentials to return a new token without making 5 steps
            with patch(
                "mem_mcp.skills.kite.skill.login_with_credentials",
                new_callable=AsyncMock,
            ) as mock_login:
                mock_login.return_value = {
                    "access_token": "NEW_ATOK",
                    "kite_user_id": "U1",
                    "public_token": "PTOK",
                }
                client = KiteClient(http=http)
                skill = KiteSkill(client=client)
                ctx = _build_skill_ctx(
                    {
                        "api_key": "AKEY",
                        "api_secret": "ASEC",
                        "access_token": "OLD_ATOK",
                        "user_id": "U1",
                        "password": "pwd123",
                        "totp_secret": "JBSWY3DPEBLW64TMMQ======",
                    }
                )
                ctx.persist_credentials = mock_persist
                out = await skill.call_tool("get_holdings", _EmptyInput(), ctx)
        assert isinstance(out, GetHoldingsOutput)
        assert out.holdings[0]["tradingsymbol"] == "RELIANCE"
        # Verify persist_credentials was called with updated creds
        assert persist_called["creds"]["access_token"] == "NEW_ATOK"
        assert persist_called["creds"]["kite_user_id"] == "U1"

    @pytest.mark.asyncio
    async def test_call_tool_token_expired_no_auto_login_creds(self) -> None:
        from mem_mcp.skills.base import SkillError
        from mem_mcp.skills.kite.client import KiteClient
        from mem_mcp.skills.kite.skill import KiteSkill, _EmptyInput

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={
                    "status": "error",
                    "message": "Invalid token",
                    "error_type": "TokenException",
                },
            )

        async with _make_mock_client(handler) as http:
            client = KiteClient(http=http)
            skill = KiteSkill(client=client)
            # No auto-login fields in creds
            ctx = _build_skill_ctx(
                {"api_key": "AKEY", "api_secret": "ASEC", "access_token": "OLD_ATOK"}
            )
            with pytest.raises(SkillError) as exc:
                await skill.call_tool("get_holdings", _EmptyInput(), ctx)
        assert "auto-login not configured" in str(exc.value)


# ==========================================================================
# Enable Kite Tool Input Validation
# ==========================================================================


class TestMemsysEnableKiteInput:
    def test_mode_c_requires_all_three(self) -> None:
        from pydantic import ValidationError

        from mem_mcp.skills.kite.enable_tool import MemsysEnableKiteInput

        # Only user_id provided
        with pytest.raises(ValidationError) as exc:
            MemsysEnableKiteInput(
                api_key="AKEY",
                api_secret="ASEC",
                user_id="U1",
            )
        assert "auto-login requires" in str(exc.value)

        # Only user_id and password provided
        with pytest.raises(ValidationError) as exc:
            MemsysEnableKiteInput(
                api_key="AKEY",
                api_secret="ASEC",
                user_id="U1",
                password="pwd",
            )
        assert "auto-login requires" in str(exc.value)

    def test_mode_c_with_orders_enabled(self) -> None:
        from mem_mcp.skills.kite.enable_tool import MemsysEnableKiteInput

        inp = MemsysEnableKiteInput(
            api_key="AKEY",
            api_secret="ASEC",
            user_id="U1",
            password="pwd123",
            totp_secret="JBSWY3DPEBLW64TMMQ======",
            orders_enabled=True,
        )
        assert inp.user_id == "U1"
        assert inp.orders_enabled is True

    def test_exactly_one_mode_enforced(self) -> None:
        from pydantic import ValidationError

        from mem_mcp.skills.kite.enable_tool import MemsysEnableKiteInput

        # Two modes provided
        with pytest.raises(ValidationError) as exc:
            MemsysEnableKiteInput(
                api_key="AKEY",
                api_secret="ASEC",
                access_token="ATOK",
                request_token="RTOK",
            )
        assert "exactly one of" in str(exc.value)
