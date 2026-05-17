"""SkillLoader — dynamically registers a skill's tools into the MCP ToolRegistry.

For each ToolDef a skill exports, builds a wrapper class that:
1. Validates input via the tool's InputModel (handled by ToolRegistry.dispatch)
2. Loads the calling tenant's credentials via SkillVault (own tenant_tx)
3. Invokes ``skill.call_tool`` with a SkillCallContext
4. Writes an audit memory (own tenant_tx, separate from credentials lookup) — so
   skill failures don't roll back the audit row
5. Returns the skill's output
"""

from __future__ import annotations

import time
from typing import Any, ClassVar
from uuid import uuid4

from pydantic import BaseModel

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.registry import ToolRegistry
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.skills.audit_memory import should_audit, write_skill_audit_memory
from mem_mcp.skills.base import (
    Skill,
    SkillCallContext,
    SkillCredentialsMissing,
    SkillError,
    SkillToolNotFound,
    ToolDef,
)
from mem_mcp.skills.vault import SkillVault


class SkillLoader:
    """Builds wrapper tool classes around a skill's tools and registers them."""

    def __init__(
        self,
        vault: SkillVault,
        audit_policies: dict[str, dict[str, bool]] | None = None,
    ) -> None:
        self._vault = vault
        self._audit_policies = audit_policies or {}

    def register_skill(self, registry: ToolRegistry, skill: Skill) -> int:
        """Register all of a skill's tools into the ToolRegistry. Returns count."""
        policy = self._audit_policies.get(skill.name)
        registered = 0
        for tool_def in skill.list_tools():
            wrapper = self._build_wrapper(skill, tool_def, policy)
            registry.register(wrapper)
            registered += 1
        return registered

    def _build_wrapper(
        self,
        skill: Skill,
        tool_def: ToolDef,
        audit_policy: dict[str, bool] | None,
    ) -> type[BaseTool]:
        full_name = f"{skill.name}_{tool_def.name}"
        scope = "memory.read" if tool_def.read_only else "memory.write"
        vault = self._vault

        class _SkillToolWrapper:
            name: ClassVar[str] = full_name
            description: ClassVar[str] = tool_def.description
            required_scope: ClassVar[str] = scope
            InputModel: ClassVar[type[BaseModel]] = tool_def.InputModel
            OutputModel: ClassVar[type[BaseModel]] = tool_def.OutputModel

            async def __call__(
                self, ctx: ToolContext, inp: BaseModel
            ) -> BaseModel:
                tool_call_id = uuid4()
                args_dict = inp.model_dump(mode="json")
                started = time.monotonic()

                # Step 1: load credentials in its own short tx
                async with tenant_tx(ctx.db_pool, ctx.tenant_id) as creds_conn:
                    try:
                        creds = await vault.load(
                            creds_conn, ctx.tenant_id, skill.name
                        )
                    except SkillCredentialsMissing as exc:
                        raise JsonRpcError(
                            -32000,
                            f"skill {skill.name!r} not enabled for this tenant",
                            data={
                                "code": "skill_credentials_missing",
                                "skill_name": skill.name,
                            },
                        ) from exc

                skill_ctx = SkillCallContext(
                    tenant_id=ctx.tenant_id,
                    identity_id=ctx.identity_id,
                    client_id=ctx.client_id,
                    request_id=ctx.request_id,
                    credentials=creds,
                    tool_call_id=tool_call_id,
                )

                # Step 2: invoke skill (outside any tx — skill calls external APIs)
                result_status: str = "success"
                error_code: str | None = None
                error_message: str | None = None
                output: BaseModel | None = None
                to_reraise: BaseException | None = None

                try:
                    output = await skill.call_tool(
                        tool_def.name, inp, skill_ctx
                    )
                except JsonRpcError as exc:
                    result_status = "error"
                    code_from_data: Any = None
                    if isinstance(exc.data, dict):
                        code_from_data = exc.data.get("code")
                    error_code = (
                        str(code_from_data)
                        if code_from_data is not None
                        else "jsonrpc_error"
                    )
                    error_message = exc.message
                    to_reraise = exc
                except SkillToolNotFound as exc:
                    result_status = "error"
                    error_code = "skill_tool_not_found"
                    error_message = str(exc)
                    to_reraise = JsonRpcError(
                        -32601,
                        f"skill tool not found: {full_name!r}",
                        data={"code": error_code},
                    )
                except SkillError as exc:
                    result_status = "error"
                    error_code = type(exc).__name__
                    error_message = str(exc)
                    to_reraise = JsonRpcError(
                        -32000,
                        f"skill error: {exc}",
                        data={"code": error_code, "skill_name": skill.name},
                    )
                except Exception as exc:
                    result_status = "error"
                    error_code = "internal_error"
                    error_message = str(exc)[:200]
                    to_reraise = JsonRpcError(
                        -32603, "internal error"
                    )

                elapsed_ms = int((time.monotonic() - started) * 1000)

                # Step 3: write audit memory in its own tx (best-effort)
                if should_audit(audit_policy, tool_def.name):
                    try:
                        async with tenant_tx(
                            ctx.db_pool, ctx.tenant_id
                        ) as audit_conn:
                            await write_skill_audit_memory(
                                conn=audit_conn,
                                ctx=skill_ctx,
                                skill_name=skill.name,
                                tool_name=tool_def.name,
                                args=args_dict,
                                result_status=result_status,
                                elapsed_ms=elapsed_ms,
                                error_code=error_code,
                                error_message=error_message,
                            )
                    except Exception:
                        # Audit failures must not break the tool call
                        pass

                if to_reraise is not None:
                    raise to_reraise
                assert output is not None
                return output

        _SkillToolWrapper.__name__ = (
            f"{skill.name.title().replace('_', '')}"
            f"{tool_def.name.title().replace('_', '')}Wrapper"
        )
        return _SkillToolWrapper  # type: ignore[return-value]


def parse_enabled_skills(setting_value: str) -> list[str]:
    """Parse comma-separated skill names from the enabled_skills setting."""
    if not setting_value.strip():
        return []
    return [s.strip().lower() for s in setting_value.split(",") if s.strip()]
