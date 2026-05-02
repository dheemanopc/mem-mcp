"""memory.stats tool — aggregate statistics and quota config."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.quotas.tiers import resolve_tier


class MemoryStatsInput(BaseModel):
    """Input for memory.stats tool (no parameters)."""

    model_config = ConfigDict(extra="forbid")


class TopTagItem(BaseModel):
    """A single tag with its count."""

    tag: str
    count: int


class TodayUsage(BaseModel):
    """Today's usage metrics."""

    writes: int
    reads: int
    embed_tokens: int


class QuotaConfig(BaseModel):
    """Tenant's quota tier configuration."""

    tier: str
    memories_limit: int
    embed_tokens_daily_limit: int
    writes_per_minute_limit: int
    reads_per_minute_limit: int


class MemoryStatsOutput(BaseModel):
    """Output for memory.stats tool."""

    total_memories: int
    by_type: dict[str, int]
    top_tags: list[TopTagItem]
    oldest: datetime | None
    newest: datetime | None
    today: TodayUsage
    quota: QuotaConfig
    request_id: str


class MemoryStatsTool(BaseTool):
    """Return aggregate statistics and quota info for the tenant."""

    name: ClassVar[str] = "memory.stats"
    required_scope: ClassVar[str] = "memory.read"
    InputModel: ClassVar[type[BaseModel]] = MemoryStatsInput
    OutputModel: ClassVar[type[BaseModel]] = MemoryStatsOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemoryStatsInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            # Query 1: Count by type
            type_rows = await conn.fetch(
                """
                SELECT type, COUNT(*) as count
                FROM memories
                WHERE tenant_id = $1 AND deleted_at IS NULL AND is_current = true
                GROUP BY type
                """,
                ctx.tenant_id,
            )
            by_type_dict: dict[str, int] = {}
            total_memories = 0
            for row in type_rows:
                count = row["count"]
                by_type_dict[row["type"]] = count
                total_memories += count

            # Query 2: Top 10 tags
            tag_rows = await conn.fetch(
                """
                SELECT tag, COUNT(*) as count
                FROM memories, LATERAL UNNEST(tags) as tag
                WHERE tenant_id = $1 AND deleted_at IS NULL AND is_current = true
                GROUP BY tag
                ORDER BY count DESC
                LIMIT 10
                """,
                ctx.tenant_id,
            )
            top_tags = [TopTagItem(tag=row["tag"], count=row["count"]) for row in tag_rows]

            # Query 3: Oldest and newest
            bounds_row = await conn.fetchrow(
                """
                SELECT MIN(created_at) as min_created, MAX(created_at) as max_created
                FROM memories
                WHERE tenant_id = $1 AND deleted_at IS NULL
                """,
                ctx.tenant_id,
            )
            oldest = bounds_row["min_created"] if bounds_row else None
            newest = bounds_row["max_created"] if bounds_row else None

            # Query 4: Today's usage
            today_row = await conn.fetchrow(
                """
                SELECT writes_count, reads_count, embed_tokens
                FROM tenant_daily_usage
                WHERE tenant_id = $1 AND usage_date = CURRENT_DATE
                """,
                ctx.tenant_id,
            )
            if today_row:
                today_writes = today_row["writes_count"]
                today_reads = today_row["reads_count"]
                today_embed = today_row["embed_tokens"]
            else:
                today_writes = 0
                today_reads = 0
                today_embed = 0

            # Query 5: Tenant tier and limits_override
            tenant_row = await conn.fetchrow(
                """
                SELECT tier, limits_override
                FROM tenants
                WHERE id = $1
                """,
                ctx.tenant_id,
            )
            if not tenant_row:
                raise JsonRpcError(-32603, "tenant not found")

            tier_name = tenant_row["tier"]
            limits_override = tenant_row["limits_override"]

            # Resolve tier limits
            tier_limits = resolve_tier(tier_name, limits_override)

            # Audit
            await ctx.deps.audit.audit(
                conn,
                action="memory.stats",
                result="success",
                tenant_id=ctx.tenant_id,
                identity_id=ctx.identity_id,
                client_id=ctx.client_id,
                request_id=ctx.request_id,
                details={
                    "total_memories": total_memories,
                    "type_count": len(by_type_dict),
                    "tag_count": len(top_tags),
                },
            )

        return MemoryStatsOutput(
            total_memories=total_memories,
            by_type=by_type_dict,
            top_tags=top_tags,
            oldest=oldest,
            newest=newest,
            today=TodayUsage(
                writes=today_writes,
                reads=today_reads,
                embed_tokens=today_embed,
            ),
            quota=QuotaConfig(
                tier=tier_name,
                memories_limit=tier_limits.memories_limit,
                embed_tokens_daily_limit=tier_limits.embed_tokens_daily,
                writes_per_minute_limit=tier_limits.writes_per_minute,
                reads_per_minute_limit=tier_limits.reads_per_minute,
            ),
            request_id=ctx.request_id,
        )
