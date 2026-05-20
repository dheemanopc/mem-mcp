"""memory.search tool — hybrid retrieval."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from mem_mcp.db import tenant_tx
from mem_mcp.embeddings.bedrock import EmbeddingError
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.memory.access_tracking import bump_access
from mem_mcp.memory.hybrid_query import (
    SEARCH_DEFAULT_W_KW,
    SEARCH_DEFAULT_W_SEM,
    SearchParams,
    hybrid_search,
)
from mem_mcp.memory.recency import recency_lambda_for

MemoryType = Literal["note", "decision", "fact", "snippet", "question"]


class MemorySearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=2048)
    tags: list[str] | None = Field(default=None, max_length=16)
    type: MemoryType | None = None
    since: datetime | None = None
    until: datetime | None = None
    include_expired: bool = False
    limit: int = Field(default=10, ge=1, le=50)
    include_history: bool = False
    recency_lambda: float | None = Field(default=None, ge=0.0, le=1.0)
    parent_id: UUID | None = None


class SearchResultItem(BaseModel):
    id: UUID
    content: str
    type: str
    tags: list[str]
    version: int
    created_at: datetime
    updated_at: datetime
    score: float
    scores_breakdown: dict[str, float]
    indexable: bool
    embedding_status: str


class MemorySearchOutput(BaseModel):
    results: list[SearchResultItem]
    query_embedding_tokens: int
    request_id: str


class MemorySearchTool(BaseTool):
    """Hybrid retrieval over memories (semantic U keyword, recency-decayed)."""

    name: ClassVar[str] = "memory_search"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memory_search"]
    required_scope: ClassVar[str] = "memory.read"
    InputModel: ClassVar[type[BaseModel]] = MemorySearchInput
    OutputModel: ClassVar[type[BaseModel]] = MemorySearchOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemorySearchInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        await ctx.deps.quotas.check_read(ctx.tenant_id)

        try:
            qembed = await ctx.deps.embeddings.embed(inp.query)
        except EmbeddingError as exc:
            raise JsonRpcError(
                -32000,
                "embedding unavailable",
                data={
                    "code": "embedding_unavailable",
                    "retry_after_seconds": exc.retry_after_seconds,
                },
            ) from exc

        recency = (
            inp.recency_lambda if inp.recency_lambda is not None else recency_lambda_for(inp.type)
        )
        params = SearchParams(
            qvec=qembed.vector,
            qtxt=inp.query,
            type_=inp.type,
            tags=list(inp.tags) if inp.tags else None,
            since=inp.since,
            until=inp.until,
            limit=inp.limit,
            recency_lambda=recency,
            w_sem=SEARCH_DEFAULT_W_SEM,
            w_kw=SEARCH_DEFAULT_W_KW,
            parent_id=inp.parent_id,
            include_expired=inp.include_expired,
        )

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            results = await hybrid_search(conn, ctx.tenant_id, params)
            await ctx.deps.audit.audit(
                conn,
                action="memory.search",
                result="success",
                tenant_id=ctx.tenant_id,
                identity_id=ctx.identity_id,
                client_id=ctx.client_id,
                request_id=ctx.request_id,
                details={
                    "query_length": len(inp.query),
                    "result_count": len(results),
                    "embed_tokens": qembed.input_tokens,
                    "type": inp.type,
                    "parent_id": str(inp.parent_id) if inp.parent_id is not None else None,
                },
            )
            await ctx.deps.quotas.increment_read(ctx.tenant_id, qembed.input_tokens)

        items = [
            SearchResultItem(
                id=r.id,
                content=r.content,
                type=r.type,
                tags=r.tags,
                version=r.version,
                created_at=r.created_at,
                updated_at=r.updated_at,
                score=r.score,
                scores_breakdown={
                    "semantic": r.sem_score,
                    "keyword": r.kw_score,
                    "recency_factor": r.recency_factor,
                },
                indexable=r.indexable,
                embedding_status=r.embedding_status,
            )
            for r in results
        ]
        # Bump access-time for every returned hit (batched, throttled).
        # Fire-and-forget; never blocks the response.
        await bump_access(ctx.db_pool, ctx.tenant_id, [r.id for r in results])
        return MemorySearchOutput(
            results=items,
            query_embedding_tokens=qembed.input_tokens,
            request_id=ctx.request_id,
        )
