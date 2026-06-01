"""memsys_refs_in — backward graph: what memories cite this one (filtered by caller access)."""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from mem_mcp.db import system_tx
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.teams.references import get_inbound_refs


class RefsInInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_id: UUID


class InboundRef(BaseModel):
    source_memory_id: str
    target_memory_id: str
    target_team_id: str
    target_fragment: int | None
    reference_kind: str
    refs_version: str


class RefsInOutput(BaseModel):
    accessible_count: int
    inaccessible_count: int
    refs: list[InboundRef]


class RefsInTool(BaseTool):
    name: ClassVar[str] = "memsys_refs_in"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memsys_refs_in"]
    required_scope: ClassVar[str] = "memory.read"
    InputModel: ClassVar[type[BaseModel]] = RefsInInput
    OutputModel: ClassVar[type[BaseModel]] = RefsInOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, RefsInInput)
        async with system_tx(ctx.db_pool) as conn:
            raw = await get_inbound_refs(conn, target_memory_id=inp.memory_id)

            accessible: list[InboundRef] = []
            inaccessible_count = 0
            for r in raw:
                allowed = await conn.fetchval(
                    "SELECT can_access_team_resource($1, $2, $3)",
                    ctx.tenant_id,
                    r["source_tenant_id"],
                    r["source_team_id"],
                )
                if not allowed:
                    inaccessible_count += 1
                    continue
                accessible.append(
                    InboundRef(
                        source_memory_id=str(r["source_memory_id"]),
                        target_memory_id=str(r["target_memory_id"]),
                        target_team_id=str(r["target_team_id"]),
                        target_fragment=r["target_fragment"],
                        reference_kind=r["reference_kind"],
                        refs_version=r["refs_version"],
                    )
                )

        return RefsInOutput(
            accessible_count=len(accessible),
            inaccessible_count=inaccessible_count,
            refs=accessible,
        )
