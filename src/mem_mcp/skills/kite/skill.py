"""KiteSkill — registers 8 Kite Connect tools into the mem-mcp skill framework.

The skill is stateless except for an injected KiteClient instance. Per-call
credentials (api_key, access_token) are loaded by the SkillLoader from the
vault and passed in via SkillCallContext.credentials.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.skills.base import (
    CredentialSpec,
    NativeSkill,
    SkillCallContext,
    SkillError,
    SkillToolNotFoundError,
    ToolDef,
)
from mem_mcp.skills.kite.auth import KiteCredentialsError, login_with_credentials
from mem_mcp.skills.kite.client import KiteApiError, KiteClient
from mem_mcp.skills.kite.ta import (
    TAIndicatorComputeInput,
    TAIndicatorComputeOutput,
    TAOhlcFetchInput,
    TAOhlcFetchOutput,
    TASessionOpenInput,
    TASessionOpenOutput,
    TASessionRefreshInput,
    TASessionRefreshOutput,
    TASessionStatusInput,
    TASessionStatusOutput,
    TASeriesFetchInput,
    TASeriesFetchOutput,
    ta_indicator_compute,
    ta_ohlc_fetch,
    ta_session_open,
    ta_session_refresh,
    ta_session_status,
    ta_series_fetch,
)

# --------------------------------------------------------------------------
# Pydantic models — input/output shapes per tool
# --------------------------------------------------------------------------


class _EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetHoldingsOutput(BaseModel):
    holdings: list[dict[str, Any]]


class GetPositionsOutput(BaseModel):
    net: list[dict[str, Any]]
    day: list[dict[str, Any]]


class GetMarginsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment: Literal["equity", "commodity"] | None = None


class GetMarginsOutput(BaseModel):
    margins: dict[str, Any]


class GetQuoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruments: list[str] = Field(..., min_length=1, max_length=500)


class GetQuoteOutput(BaseModel):
    quotes: dict[str, Any]


class GetHistoricalDataInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_token: int
    interval: Literal[
        "minute",
        "3minute",
        "5minute",
        "15minute",
        "30minute",
        "60minute",
        "day",
        "week",
        "month",
    ]
    from_date: str
    to_date: str
    continuous: bool = False
    oi: bool = False


class GetHistoricalDataOutput(BaseModel):
    candles: list[list[Any]]


class GetOrdersOutput(BaseModel):
    orders: list[dict[str, Any]]


class PlaceOrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variety: Literal["regular", "amo", "co", "iceberg", "auction"]
    tradingsymbol: str
    exchange: Literal["NSE", "BSE", "NFO", "MCX", "CDS", "BFO", "BCD"]
    transaction_type: Literal["BUY", "SELL"]
    quantity: int = Field(..., gt=0)
    product: Literal["CNC", "MIS", "NRML", "CO"]
    order_type: Literal["MARKET", "LIMIT", "SL", "SL-M"]
    price: float | None = None
    trigger_price: float | None = None
    validity: Literal["DAY", "IOC", "TTL"] = "DAY"
    disclosed_quantity: int | None = None
    tag: str | None = None


class PlaceOrderOutput(BaseModel):
    order_id: str


class CancelOrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variety: Literal["regular", "amo", "co", "iceberg", "auction"]
    order_id: str


class CancelOrderOutput(BaseModel):
    order_id: str


# --------------------------------------------------------------------------
# Audit policy — which Kite tool calls write audit memories
# --------------------------------------------------------------------------

KITE_AUDIT_POLICY: dict[str, bool] = {
    "get_quote": False,
    "get_historical_data": False,
    "get_holdings": True,
    "get_positions": True,
    "get_margins": True,
    "get_orders": True,
    "place_order": True,
    "cancel_order": True,
}


# --------------------------------------------------------------------------
# KiteSkill
# --------------------------------------------------------------------------


class KiteSkill(NativeSkill):
    """Native skill exposing 8 Kite Connect tools."""

    name: ClassVar[str] = "kite"

    def __init__(self, client: KiteClient | None = None) -> None:
        self._client = client or KiteClient()

    def required_credentials(self) -> list[CredentialSpec]:
        return [
            CredentialSpec(name="api_key", description="Kite Connect app API key"),
            CredentialSpec(
                name="api_secret",
                description="Kite Connect app API secret (used only at onboarding for token exchange)",
            ),
            CredentialSpec(
                name="access_token",
                description="Daily access_token from Kite session/token exchange",
            ),
        ]

    async def _call_with_auto_refresh(
        self,
        ctx: SkillCallContext,
        do_call: Callable[[str, str], Awaitable[Any]],
    ) -> Any:
        """Run a Kite call (do_call(api_key, access_token) -> data). On TokenException,
        re-login from stored credentials if available, persist, retry once."""
        creds = ctx.credentials
        try:
            return await do_call(creds["api_key"], creds["access_token"])
        except KiteApiError as exc:
            if exc.error_type != "TokenException":
                raise
            # Token expired. Can we self-refresh?
            if not all(creds.get(k) for k in ("user_id", "password", "totp_secret", "api_secret")):
                raise SkillError(
                    "kite access_token expired and auto-login not configured; re-onboard via memsys_enable_kite"
                ) from exc
            try:
                login_result = await login_with_credentials(
                    api_key=creds["api_key"],
                    api_secret=creds["api_secret"],
                    user_id=creds["user_id"],
                    password=creds["password"],
                    totp_secret=creds["totp_secret"],
                )
            except KiteCredentialsError as auth_exc:
                raise SkillError(f"kite auto-login failed: {auth_exc}") from auth_exc
            new_access = login_result["access_token"]
            if not new_access:
                raise SkillError("kite auto-login returned no access_token") from exc
            # Update in-memory creds
            creds["access_token"] = new_access
            if login_result.get("kite_user_id"):
                creds["kite_user_id"] = login_result["kite_user_id"]
            # Persist (best-effort)
            if ctx.persist_credentials is not None:
                try:
                    await ctx.persist_credentials(creds)
                except Exception:
                    pass  # don't fail the retry just because vault write hiccupped
            # Retry once
            return await do_call(creds["api_key"], new_access)

    def list_tools(self) -> list[ToolDef]:
        return [
            ToolDef(
                name="get_holdings",
                description=TOOL_DESCRIPTIONS["kite_get_holdings"],
                InputModel=_EmptyInput,
                OutputModel=GetHoldingsOutput,
                read_only=True,
            ),
            ToolDef(
                name="get_positions",
                description=TOOL_DESCRIPTIONS["kite_get_positions"],
                InputModel=_EmptyInput,
                OutputModel=GetPositionsOutput,
                read_only=True,
            ),
            ToolDef(
                name="get_margins",
                description=TOOL_DESCRIPTIONS["kite_get_margins"],
                InputModel=GetMarginsInput,
                OutputModel=GetMarginsOutput,
                read_only=True,
            ),
            ToolDef(
                name="get_quote",
                description=TOOL_DESCRIPTIONS["kite_get_quote"],
                InputModel=GetQuoteInput,
                OutputModel=GetQuoteOutput,
                read_only=True,
            ),
            ToolDef(
                name="get_historical_data",
                description=TOOL_DESCRIPTIONS["kite_get_historical_data"],
                InputModel=GetHistoricalDataInput,
                OutputModel=GetHistoricalDataOutput,
                read_only=True,
            ),
            ToolDef(
                name="get_orders",
                description=TOOL_DESCRIPTIONS["kite_get_orders"],
                InputModel=_EmptyInput,
                OutputModel=GetOrdersOutput,
                read_only=True,
            ),
            ToolDef(
                name="place_order",
                description=TOOL_DESCRIPTIONS["kite_place_order"],
                InputModel=PlaceOrderInput,
                OutputModel=PlaceOrderOutput,
                read_only=False,
            ),
            ToolDef(
                name="cancel_order",
                description=TOOL_DESCRIPTIONS["kite_cancel_order"],
                InputModel=CancelOrderInput,
                OutputModel=CancelOrderOutput,
                read_only=False,
            ),
            # Phase 2: TA session tools
            ToolDef(
                name="ta_session_open",
                description=TOOL_DESCRIPTIONS["kite_ta_session_open"],
                InputModel=TASessionOpenInput,
                OutputModel=TASessionOpenOutput,
                read_only=True,
            ),
            ToolDef(
                name="ta_indicator_compute",
                description=TOOL_DESCRIPTIONS["kite_ta_indicator_compute"],
                InputModel=TAIndicatorComputeInput,
                OutputModel=TAIndicatorComputeOutput,
                read_only=True,
            ),
            ToolDef(
                name="ta_series_fetch",
                description=TOOL_DESCRIPTIONS["kite_ta_series_fetch"],
                InputModel=TASeriesFetchInput,
                OutputModel=TASeriesFetchOutput,
                read_only=True,
            ),
            ToolDef(
                name="ta_ohlc_fetch",
                description=TOOL_DESCRIPTIONS["kite_ta_ohlc_fetch"],
                InputModel=TAOhlcFetchInput,
                OutputModel=TAOhlcFetchOutput,
                read_only=True,
            ),
            ToolDef(
                name="ta_session_status",
                description=TOOL_DESCRIPTIONS["kite_ta_session_status"],
                InputModel=TASessionStatusInput,
                OutputModel=TASessionStatusOutput,
                read_only=True,
            ),
            ToolDef(
                name="ta_session_refresh",
                description=TOOL_DESCRIPTIONS["kite_ta_session_refresh"],
                InputModel=TASessionRefreshInput,
                OutputModel=TASessionRefreshOutput,
                read_only=True,
            ),
        ]

    async def call_tool(self, tool_name: str, args: BaseModel, ctx: SkillCallContext) -> BaseModel:
        creds = ctx.credentials
        try:
            _ = creds["api_key"]
            _ = creds["access_token"]
        except KeyError as exc:
            raise SkillError(f"missing required Kite credential field: {exc}") from exc

        # Gate place_order and cancel_order on orders_enabled flag
        if tool_name in ("place_order", "cancel_order") and not creds.get("orders_enabled", False):
            raise SkillError(
                "trading is not enabled for this tenant; re-onboard with orders_enabled=true via memsys_enable_kite"
            )

        try:
            if tool_name == "get_holdings":
                holdings_data: Any = await self._call_with_auto_refresh(
                    ctx, lambda ak, at: self._client.get_holdings(api_key=ak, access_token=at)
                )
                return GetHoldingsOutput(holdings=list(holdings_data or []))

            if tool_name == "get_positions":
                pos_data: Any = await self._call_with_auto_refresh(
                    ctx, lambda ak, at: self._client.get_positions(api_key=ak, access_token=at)
                )
                return GetPositionsOutput(
                    net=list((pos_data or {}).get("net") or []),
                    day=list((pos_data or {}).get("day") or []),
                )

            if tool_name == "get_margins":
                assert isinstance(args, GetMarginsInput)
                margins_data: Any = await self._call_with_auto_refresh(
                    ctx,
                    lambda ak, at: self._client.get_margins(
                        api_key=ak, access_token=at, segment=args.segment
                    ),
                )
                return GetMarginsOutput(margins=dict(margins_data or {}))

            if tool_name == "get_quote":
                assert isinstance(args, GetQuoteInput)
                quote_data: Any = await self._call_with_auto_refresh(
                    ctx,
                    lambda ak, at: self._client.get_quote(
                        api_key=ak, access_token=at, instruments=args.instruments
                    ),
                )
                return GetQuoteOutput(quotes=dict(quote_data or {}))

            if tool_name == "get_historical_data":
                assert isinstance(args, GetHistoricalDataInput)
                hist_data: Any = await self._call_with_auto_refresh(
                    ctx,
                    lambda ak, at: self._client.get_historical_data(
                        api_key=ak,
                        access_token=at,
                        instrument_token=args.instrument_token,
                        interval=args.interval,
                        from_date=args.from_date,
                        to_date=args.to_date,
                        continuous=args.continuous,
                        oi=args.oi,
                    ),
                )
                return GetHistoricalDataOutput(candles=list((hist_data or {}).get("candles") or []))

            if tool_name == "get_orders":
                orders_data: Any = await self._call_with_auto_refresh(
                    ctx, lambda ak, at: self._client.get_orders(api_key=ak, access_token=at)
                )
                return GetOrdersOutput(orders=list(orders_data or []))

            if tool_name == "place_order":
                assert isinstance(args, PlaceOrderInput)
                order_params: dict[str, Any] = {
                    "tradingsymbol": args.tradingsymbol,
                    "exchange": args.exchange,
                    "transaction_type": args.transaction_type,
                    "quantity": args.quantity,
                    "product": args.product,
                    "order_type": args.order_type,
                    "validity": args.validity,
                }
                if args.price is not None:
                    order_params["price"] = args.price
                if args.trigger_price is not None:
                    order_params["trigger_price"] = args.trigger_price
                if args.disclosed_quantity is not None:
                    order_params["disclosed_quantity"] = args.disclosed_quantity
                if args.tag is not None:
                    order_params["tag"] = args.tag
                placed_order_data: Any = await self._call_with_auto_refresh(
                    ctx,
                    lambda ak, at: self._client.place_order(
                        api_key=ak,
                        access_token=at,
                        variety=args.variety,
                        order_params=order_params,
                    ),
                )
                return PlaceOrderOutput(order_id=str((placed_order_data or {}).get("order_id", "")))

            if tool_name == "cancel_order":
                assert isinstance(args, CancelOrderInput)
                cancel_order_data: Any = await self._call_with_auto_refresh(
                    ctx,
                    lambda ak, at: self._client.cancel_order(
                        api_key=ak,
                        access_token=at,
                        variety=args.variety,
                        order_id=args.order_id,
                    ),
                )
                return CancelOrderOutput(
                    order_id=str((cancel_order_data or {}).get("order_id", args.order_id))
                )

            # Phase 2: TA session tools
            if tool_name == "ta_session_open":
                assert isinstance(args, TASessionOpenInput)
                return await ta_session_open(self._client, ctx, args)

            if tool_name == "ta_indicator_compute":
                assert isinstance(args, TAIndicatorComputeInput)
                return await ta_indicator_compute(self._client, ctx, args)

            if tool_name == "ta_series_fetch":
                assert isinstance(args, TASeriesFetchInput)
                return await ta_series_fetch(self._client, ctx, args)

            if tool_name == "ta_ohlc_fetch":
                assert isinstance(args, TAOhlcFetchInput)
                return await ta_ohlc_fetch(self._client, ctx, args)

            if tool_name == "ta_session_status":
                assert isinstance(args, TASessionStatusInput)
                return await ta_session_status(self._client, ctx, args)

            if tool_name == "ta_session_refresh":
                assert isinstance(args, TASessionRefreshInput)
                return await ta_session_refresh(self._client, ctx, args)

            raise SkillToolNotFoundError(f"kite skill has no tool named {tool_name!r}")

        except KiteApiError as exc:
            # Wrap Kite errors as SkillError so the loader can convert to JsonRpcError
            raise SkillError(f"kite_{tool_name}: {exc}") from exc

    async def health_check(self) -> bool:
        return True
