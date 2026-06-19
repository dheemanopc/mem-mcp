"""memory.search_chunks tool — chunk-level semantic retrieval.

Google-search-style result previews (memsys proposal 3e66b43c): vector
search at chunk level so a semantic match localizes to the passage that
matched, instead of forcing the whole memory into the caller's context.
Provenance (memory_id, type, tags, created_at, chunk_index/total_chunks)
is stapled to every snippet; escalation is memory_get for the full body.

Chunks are built asynchronously by the chunk_build job — results are
eventually consistent with writes (typically within minutes).
"""

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

MemoryType = Literal["note", "decision", "fact", "snippet", "question", "position", "challenge"]

# Candidate pool before per-memory capping; bounded so the window function
# never runs over the whole table.
_CANDIDATE_POOL = 200

_CHUNK_SEARCH_SQL = """
WITH hits AS (
    SELECT c.memory_id, c.chunk_index, c.content AS chunk_content,
           1 - (c.embedding <=> $1::vector) AS score,
           m.type, m.tags, m.created_at,
           ROW_NUMBER() OVER (
               PARTITION BY c.memory_id
               ORDER BY c.embedding <=> $1::vector
           ) AS rn
    FROM memory_chunks c
    JOIN memories m ON m.id = c.memory_id
    WHERE c.tenant_id = $2
      AND c.embedding IS NOT NULL
      AND m.deleted_at IS NULL
      AND m.is_current = true
      AND m.indexable = true
      AND (m.expires_at IS NULL OR m.expires_at > NOW())
      AND ($3::text IS NULL OR m.type = $3)
      AND ($4::text[] IS NULL OR m.tags @> $4)
    ORDER BY c.embedding <=> $1::vector
    LIMIT $5
),
totals AS (
    SELECT memory_id, COUNT(*) AS total_chunks
    FROM memory_chunks
    WHERE memory_id IN (SELECT DISTINCT memory_id FROM hits)
    GROUP BY memory_id
)
SELECT h.memory_id, h.chunk_index, h.chunk_content, h.score,
       h.type, h.tags, h.created_at, t.total_chunks
FROM hits h
JOIN totals t ON t.memory_id = h.memory_id
WHERE h.rn <= $6
ORDER BY h.score DESC
LIMIT $7
""".strip()


class MemorySearchChunksInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=2048)
    type: MemoryType | None = None
    tags: list[str] | None = Field(default=None, max_length=16)
    limit: int = Field(default=10, ge=1, le=50)
    # Best N chunks per memory — long memories often match in several places
    # and the second match is frequently the needed one.
    per_memory: int = Field(default=2, ge=1, le=5)


class ChunkResultItem(BaseModel):
    memory_id: UUID
    chunk_index: int
    total_chunks: int
    snippet: str
    score: float
    type: str
    tags: list[str]
    created_at: datetime


class MemorySearchChunksOutput(BaseModel):
    results: list[ChunkResultItem]
    query_embedding_tokens: int
    request_id: str


class MemorySearchChunksTool(BaseTool):
    """Chunk-level semantic retrieval with snippet previews."""

    name: ClassVar[str] = "memory_search_chunks"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memory_search_chunks"]
    required_scope: ClassVar[str] = "memory.read"
    InputModel: ClassVar[type[BaseModel]] = MemorySearchChunksInput
    OutputModel: ClassVar[type[BaseModel]] = MemorySearchChunksOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemorySearchChunksInput)
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

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            rows = await conn.fetch(
                _CHUNK_SEARCH_SQL,
                qembed.vector,  # $1
                ctx.tenant_id,  # $2
                inp.type,  # $3
                list(inp.tags) if inp.tags else None,  # $4
                _CANDIDATE_POOL,  # $5
                inp.per_memory,  # $6
                inp.limit,  # $7
            )
            await ctx.deps.audit.audit(
                conn,
                action="memory.search_chunks",
                result="success",
                tenant_id=ctx.tenant_id,
                identity_id=ctx.identity_id,
                client_id=ctx.client_id,
                request_id=ctx.request_id,
                details={
                    "query_length": len(inp.query),
                    "result_count": len(rows),
                    "embed_tokens": qembed.input_tokens,
                    "type": inp.type,
                },
            )
            await ctx.deps.quotas.increment_read(ctx.tenant_id, qembed.input_tokens)

        items = [
            ChunkResultItem(
                memory_id=r["memory_id"],
                chunk_index=int(r["chunk_index"]),
                total_chunks=int(r["total_chunks"]),
                snippet=r["chunk_content"],
                score=float(r["score"]),
                type=r["type"],
                tags=list(r["tags"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]
        await bump_access(ctx.db_pool, ctx.tenant_id, list({r["memory_id"] for r in rows}))
        return MemorySearchChunksOutput(
            results=items,
            query_embedding_tokens=qembed.input_tokens,
            request_id=ctx.request_id,
        )
