"""KitePlugin — exposes Kite Connect tools via the Plugin SDK.

Phase 3 refactor: kite tools now flow through the new Plugin SDK contract.
Existing SkillVault credential access is preserved (Phase 3.4 switches to ctx.credentials).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from mem_mcp.auth.permissions import Permission
from mem_mcp.db import tenant_tx
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.plugins.contract import Plugin, ToolRegistry
from mem_mcp.skills.kite.skill import (
    CancelOrderInput,
    GetHistoricalDataInput,
    GetMarginsInput,
    GetQuoteInput,
    KiteSkill,
    PlaceOrderInput,
    _EmptyInput,
)
from mem_mcp.skills.kite.ta import (
    TAIndicatorComputeInput,
    TAOhlcFetchInput,
    TASeriesFetchInput,
    TASessionOpenInput,
    TASessionRefreshInput,
    TASessionStatusInput,
)
from mem_mcp.skills.vault import SkillVault, SkillVaultDisabledError

log = logging.getLogger(__name__)


class KitePlugin(Plugin):
    """Kite Connect MCP tools via the Plugin SDK.

    Credential scope is 'tenant' — each tenant can have their own api_key/access_token.
    Tools are registered via the new ToolRegistry contract; handlers load credentials
    from SkillVault and delegate to the existing KiteSkill implementation.
    """

    id: str = "kite"
    display_name: str = "Zerodha Kite"
    api_version: str = "1"
    credential_scope: str = "tenant"  # type: ignore[assignment]

    def __init__(self) -> None:
        self._kite_skill = KiteSkill()
        self._vault: SkillVault | None = None

    def register_tools(self, registry: ToolRegistry) -> None:
        """Declare all kite tools into the plugin registry."""
        # Try to initialize vault; if it fails, we'll report and skip tool registration
        try:
            from mem_mcp.config import get_settings

            s = get_settings()
            self._vault = SkillVault.from_settings(s)
        except SkillVaultDisabledError:
            log.warning("kite_plugin_vault_disabled")
            return
        except Exception:
            log.exception("kite_plugin_vault_init_failed")
            return

        vault = self._vault
        assert vault is not None  # guaranteed by successful assignment above

        # Create handler factory
        def make_handler(tool_name: str) -> Any:
            """Create a handler for the given tool name."""

            async def handler(inp: BaseModel, tenant_id: UUID, request_id: str) -> BaseModel:
                """Generic handler that loads credentials and calls the skill."""
                from mem_mcp.db import get_pool
                from mem_mcp.skills.base import SkillCallContext

                pool = get_pool()
                async with tenant_tx(pool, tenant_id) as conn:
                    creds = await vault.load(conn, tenant_id, "kite")

                skill_ctx = SkillCallContext(
                    tenant_id=tenant_id,
                    identity_id=None,  # type: ignore
                    client_id="plugin",
                    request_id=request_id,
                    credentials=creds,
                    tool_call_id=None,  # type: ignore
                    persist_credentials=None,
                )
                return await self._kite_skill.call_tool(tool_name, inp, skill_ctx)

            return handler

        # Read-only tools
        registry.register(
            name="get_holdings",
            handler=make_handler("get_holdings"),
            description=TOOL_DESCRIPTIONS["kite_get_holdings"],
            input_schema=_EmptyInput,
            required_permission=Permission.TEAM_READ_MEMORY,
        )
        registry.register(
            name="get_positions",
            handler=make_handler("get_positions"),
            description=TOOL_DESCRIPTIONS["kite_get_positions"],
            input_schema=_EmptyInput,
            required_permission=Permission.TEAM_READ_MEMORY,
        )
        registry.register(
            name="get_margins",
            handler=make_handler("get_margins"),
            description=TOOL_DESCRIPTIONS["kite_get_margins"],
            input_schema=GetMarginsInput,
            required_permission=Permission.TEAM_READ_MEMORY,
        )
        registry.register(
            name="get_quote",
            handler=make_handler("get_quote"),
            description=TOOL_DESCRIPTIONS["kite_get_quote"],
            input_schema=GetQuoteInput,
            required_permission=Permission.TEAM_READ_MEMORY,
        )
        registry.register(
            name="get_historical_data",
            handler=make_handler("get_historical_data"),
            description=TOOL_DESCRIPTIONS["kite_get_historical_data"],
            input_schema=GetHistoricalDataInput,
            required_permission=Permission.TEAM_READ_MEMORY,
        )
        registry.register(
            name="get_orders",
            handler=make_handler("get_orders"),
            description=TOOL_DESCRIPTIONS["kite_get_orders"],
            input_schema=_EmptyInput,
            required_permission=Permission.TEAM_READ_MEMORY,
        )

        # Write tools
        registry.register(
            name="place_order",
            handler=make_handler("place_order"),
            description=TOOL_DESCRIPTIONS["kite_place_order"],
            input_schema=PlaceOrderInput,
            required_permission=Permission.TEAM_WRITE_MEMORY,
        )
        registry.register(
            name="cancel_order",
            handler=make_handler("cancel_order"),
            description=TOOL_DESCRIPTIONS["kite_cancel_order"],
            input_schema=CancelOrderInput,
            required_permission=Permission.TEAM_WRITE_MEMORY,
        )

        # TA tools (Phase 2 extension)
        registry.register(
            name="ta_session_open",
            handler=make_handler("ta_session_open"),
            description=TOOL_DESCRIPTIONS["kite_ta_session_open"],
            input_schema=TASessionOpenInput,
            required_permission=Permission.TEAM_READ_MEMORY,
        )
        registry.register(
            name="ta_session_status",
            handler=make_handler("ta_session_status"),
            description=TOOL_DESCRIPTIONS["kite_ta_session_status"],
            input_schema=TASessionStatusInput,
            required_permission=Permission.TEAM_READ_MEMORY,
        )
        registry.register(
            name="ta_session_refresh",
            handler=make_handler("ta_session_refresh"),
            description=TOOL_DESCRIPTIONS["kite_ta_session_refresh"],
            input_schema=TASessionRefreshInput,
            required_permission=Permission.TEAM_READ_MEMORY,
        )
        registry.register(
            name="ta_ohlc_fetch",
            handler=make_handler("ta_ohlc_fetch"),
            description=TOOL_DESCRIPTIONS["kite_ta_ohlc_fetch"],
            input_schema=TAOhlcFetchInput,
            required_permission=Permission.TEAM_READ_MEMORY,
        )
        registry.register(
            name="ta_series_fetch",
            handler=make_handler("ta_series_fetch"),
            description=TOOL_DESCRIPTIONS["kite_ta_series_fetch"],
            input_schema=TASeriesFetchInput,
            required_permission=Permission.TEAM_READ_MEMORY,
        )
        registry.register(
            name="ta_indicator_compute",
            handler=make_handler("ta_indicator_compute"),
            description=TOOL_DESCRIPTIONS["kite_ta_indicator_compute"],
            input_schema=TAIndicatorComputeInput,
            required_permission=Permission.TEAM_READ_MEMORY,
        )

        log.info("kite_plugin_tools_registered")
