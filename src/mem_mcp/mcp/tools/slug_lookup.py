"""memsys_slug_lookup — cross-tenant slug-to-memory lookup with access gating."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from mem_mcp.db import system_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.teams.slugs import lookup_slug


class MemsysSlugLookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: UUID
    resource_type: Literal["decision", "fact"]
    slug: str = Field(..., min_length=1, max_length=64)


class MemsysSlugLookupOutput(BaseModel):
    memory_id: UUID | None
    title: str | None
    updated_at: datetime | None


class MemsysSlugLookupTool(BaseTool):
    """Look up a memory by slug within a team (opaque cross-tenant access control)."""

    name: ClassVar[str] = "memsys_slug_lookup"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memsys_slug_lookup"]
    required_scope: ClassVar[str] = "memory.read"
    InputModel: ClassVar[type[BaseModel]] = MemsysSlugLookupInput
    OutputModel: ClassVar[type[BaseModel]] = MemsysSlugLookupOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemsysSlugLookupInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        async with system_tx(ctx.db_pool) as conn:
            # Access check: caller must have access to team_id via user_effective_team_access
            access = await conn.fetchval(
                """
                SELECT 1 FROM user_effective_team_access
                 WHERE user_id = $1 AND resource_team_id = $2
                """,
                ctx.tenant_id,
                inp.team_id,
            )
            if access is None:
                # No access — return opaque null trio (do not leak existence)
                await ctx.deps.audit.audit(
                    conn,
                    action="memsys.slug_lookup",
                    result="denied",
                    tenant_id=ctx.tenant_id,
                    identity_id=ctx.identity_id,
                    client_id=ctx.client_id,
                    target_id=None,
                    target_kind="memory",
                    request_id=ctx.request_id,
                    details={
                        "team_id": str(inp.team_id),
                        "resource_type": inp.resource_type,
                        "slug": inp.slug,
                        "found": False,
                    },
                )
                return MemsysSlugLookupOutput(memory_id=None, title=None, updated_at=None)

            # Access granted — lookup slug
            row = await lookup_slug(
                conn,
                team_id=inp.team_id,
                resource_type=inp.resource_type,
                slug=inp.slug,
            )

            found = row is not None
            memory_id: UUID | None = row["memory_id"] if row else None  # type: ignore[assignment]
            title: str | None = row["title"] if row else None  # type: ignore[assignment]
            updated_at: datetime | None = row["updated_at"] if row else None  # type: ignore[assignment]

            await ctx.deps.audit.audit(
                conn,
                action="memsys.slug_lookup",
                result="success",
                tenant_id=ctx.tenant_id,
                identity_id=ctx.identity_id,
                client_id=ctx.client_id,
                target_id=memory_id,
                target_kind="memory",
                request_id=ctx.request_id,
                details={
                    "team_id": str(inp.team_id),
                    "resource_type": inp.resource_type,
                    "slug": inp.slug,
                    "found": found,
                },
            )

            return MemsysSlugLookupOutput(
                memory_id=memory_id,
                title=title,
                updated_at=updated_at,
            )
