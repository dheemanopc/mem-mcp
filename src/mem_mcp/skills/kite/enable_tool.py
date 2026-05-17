"""memsys_enable_kite tool — onboard a tenant's Kite Connect credentials.

Flow:
1. Tenant calls with api_key + api_secret + one of (request_token | access_token).
2. If request_token provided: POST /session/token to exchange for access_token.
3. Store (api_key, api_secret, access_token) encrypted in tenant_skill_credentials.
4. Smoke-test by calling get_holdings.
5. Return status + Kite user_id + smoke result.

This is a regular MCP tool (not a skill tool), registered alongside the 12 memory
tools. It needs no skill-loader path because its job is to *enable* a skill.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, model_validator

from mem_mcp.config import get_settings
from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.skills.kite.auth import KiteAuthError, exchange_request_token
from mem_mcp.skills.kite.client import KiteApiError, KiteClient
from mem_mcp.skills.vault import SkillVault, SkillVaultDisabledError, SkillVaultError


class MemsysEnableKiteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str
    api_secret: str
    request_token: str | None = None
    access_token: str | None = None

    @model_validator(mode="after")
    def _exactly_one_token(self) -> MemsysEnableKiteInput:
        rt = self.request_token
        at = self.access_token
        if (rt is None) == (at is None):
            raise ValueError(
                "exactly one of request_token or access_token must be provided"
            )
        return self


class MemsysEnableKiteOutput(BaseModel):
    status: str
    user_id: str | None
    smoke_test_result: str
    request_id: str


class MemsysEnableKiteTool(BaseTool):
    """Onboard or refresh Kite Connect credentials for the calling tenant."""

    name: ClassVar[str] = "memsys_enable_kite"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memsys_enable_kite"]
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = MemsysEnableKiteInput
    OutputModel: ClassVar[type[BaseModel]] = MemsysEnableKiteOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemsysEnableKiteInput)

        # Load vault from settings (master key cached at boot)
        try:
            vault = SkillVault.from_settings(get_settings())
        except SkillVaultDisabledError as exc:
            raise JsonRpcError(
                -32000,
                "skill framework not enabled — vault master key unset",
                data={"code": "skill_vault_disabled"},
            ) from exc
        except SkillVaultError as exc:
            raise JsonRpcError(
                -32000,
                f"skill vault error: {exc}",
                data={"code": "skill_vault_error"},
            ) from exc

        # Step 1: resolve access_token
        kite_user_id: str | None = None
        if inp.request_token is not None:
            try:
                token_data = await exchange_request_token(
                    inp.api_key, inp.request_token, inp.api_secret
                )
            except KiteAuthError as exc:
                raise JsonRpcError(
                    -32000,
                    f"kite auth failed: {exc}",
                    data={
                        "code": "kite_auth_failed",
                        "error_type": exc.error_type,
                    },
                ) from exc
            access_token = token_data.get("access_token")
            kite_user_id = token_data.get("user_id")
            if not access_token:
                raise JsonRpcError(
                    -32603,
                    "kite returned no access_token after exchange",
                    data={"code": "kite_no_access_token"},
                )
        else:
            assert inp.access_token is not None  # model_validator guarantees
            access_token = inp.access_token

        # Step 2: store encrypted in vault
        creds: dict[str, Any] = {
            "api_key": inp.api_key,
            "api_secret": inp.api_secret,
            "access_token": access_token,
        }
        if kite_user_id is not None:
            creds["user_id"] = kite_user_id
        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            await vault.store(conn, ctx.tenant_id, "kite", creds)

        # Step 3: smoke test — call get_holdings
        smoke_result: str
        smoke_client = KiteClient()
        try:
            holdings = await smoke_client.get_holdings(
                api_key=inp.api_key, access_token=access_token
            )
            smoke_result = (
                f"ok — {len(holdings) if isinstance(holdings, list) else 0} holdings returned"
            )
        except KiteApiError as exc:
            smoke_result = f"smoke test failed: {exc}"
            # Credentials are stored but the smoke test failed — return a non-fatal
            # response so the caller knows. They can retry with a fresh token.
            return MemsysEnableKiteOutput(
                status="stored_but_smoke_failed",
                user_id=kite_user_id,
                smoke_test_result=smoke_result,
                request_id=ctx.request_id,
            )
        except Exception as exc:
            smoke_result = f"smoke test exception: {type(exc).__name__}: {exc}"
            return MemsysEnableKiteOutput(
                status="stored_but_smoke_failed",
                user_id=kite_user_id,
                smoke_test_result=smoke_result,
                request_id=ctx.request_id,
            )

        return MemsysEnableKiteOutput(
            status="enabled",
            user_id=kite_user_id,
            smoke_test_result=smoke_result,
            request_id=ctx.request_id,
        )
