"""memory.write tool — store a memory (or merge into existing duplicate)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mem_mcp.db import tenant_tx
from mem_mcp.embeddings.bedrock import EmbeddingError
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.memory.dedupe import check_dup
from mem_mcp.memory.normalize import hash_content
from mem_mcp.memory.versioning import VERSIONED_TYPES

_TAG_RE = re.compile(r"^[a-zA-Z0-9_:.-]+$")
MemoryType = Literal["note", "decision", "fact", "snippet", "question"]


class MemoryWriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=32_768)
    type: MemoryType = "note"
    tags: list[str] = Field(default_factory=list, max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict)
    supersedes: UUID | None = None
    force_new: bool = False

    @field_validator("tags", mode="after")
    @classmethod
    def _validate_tags(cls, v: list[str]) -> list[str]:
        for t in v:
            if not isinstance(t, str):
                raise ValueError(f"tag must be string, got {type(t).__name__}")
            if not 1 <= len(t) <= 64:
                raise ValueError(f"tag length out of range: {t!r}")
            if not _TAG_RE.match(t):
                raise ValueError(f"tag contains invalid chars: {t!r}")
        if len(set(v)) != len(v):
            raise ValueError("duplicate tags")
        return v

    @field_validator("supersedes", mode="after")
    @classmethod
    def _validate_supersedes(cls, v: UUID | None, info: Any) -> UUID | None:
        if v is not None:
            type_ = info.data.get("type", "note")
            if type_ not in VERSIONED_TYPES:
                raise ValueError(f"supersedes only valid for {VERSIONED_TYPES}, got {type_!r}")
        return v


class MemoryWriteOutput(BaseModel):
    id: UUID
    version: int
    deduped: bool
    merged_into: UUID | None
    created_at: datetime
    request_id: str


class MemoryWriteTool(BaseTool):
    """Store a memory (or merge into existing duplicate)."""

    name: ClassVar[str] = "memory.write"
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = MemoryWriteInput
    OutputModel: ClassVar[type[BaseModel]] = MemoryWriteOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemoryWriteInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        # Quota check (NoopQuotas is the v1 default; real enforcer in T-7.9)
        await ctx.deps.quotas.check_write(ctx.tenant_id, len(inp.content))

        content_hash = hash_content(inp.content)

        # Embed (Bedrock; can raise EmbeddingError)
        try:
            embed = await ctx.deps.embeddings.embed(inp.content)
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
            # Dedupe (unless caller forced new)
            existing = None
            if not inp.force_new:
                existing = await check_dup(
                    conn,
                    ctx.tenant_id,
                    content_hash,
                    embed.vector,
                    inp.type,
                )

            if existing is not None and not inp.force_new:
                # Merge into existing — union tags, bump updated_at
                row = await conn.fetchrow(
                    """
                    UPDATE memories
                    SET tags = ARRAY(SELECT DISTINCT unnest(tags || $2::text[])),
                        updated_at = now()
                    WHERE id = $1 AND tenant_id = $3
                    RETURNING id, version, created_at
                    """,
                    existing.existing_id,
                    inp.tags,
                    ctx.tenant_id,
                )
                if row is None:
                    raise JsonRpcError(-32603, "dedupe target row vanished")
                await ctx.deps.audit.audit(
                    conn,
                    action="memory.dedupe_merged",
                    result="success",
                    tenant_id=ctx.tenant_id,
                    identity_id=ctx.identity_id,
                    client_id=ctx.client_id,
                    target_id=existing.existing_id,
                    target_kind="memory",
                    request_id=ctx.request_id,
                    details={
                        "kind": existing.kind,
                        "embed_tokens": embed.input_tokens,
                    },
                )
                return MemoryWriteOutput(
                    id=existing.existing_id,
                    version=int(row["version"]),
                    deduped=True,
                    merged_into=existing.existing_id,
                    created_at=row["created_at"],
                    request_id=ctx.request_id,
                )

            # supersede branch (versioned types only)
            if inp.supersedes is not None:
                if inp.type not in VERSIONED_TYPES:
                    raise JsonRpcError(
                        -32602,
                        "supersedes only valid for decision or fact",
                        data={
                            "errors": [
                                {
                                    "path": "supersedes",
                                    "message": "type must be decision or fact",
                                }
                            ]
                        },
                    )
                old = await conn.fetchrow(
                    "SELECT id, version, type FROM memories WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL",
                    inp.supersedes,
                    ctx.tenant_id,
                )
                if old is None:
                    raise JsonRpcError(
                        -32602,
                        "supersede target not found",
                        data={
                            "errors": [
                                {
                                    "path": "supersedes",
                                    "message": "memory not found",
                                }
                            ]
                        },
                    )
                if old["type"] != inp.type:
                    raise JsonRpcError(
                        -32602,
                        "supersede type mismatch",
                        data={
                            "errors": [
                                {
                                    "path": "supersedes",
                                    "message": f"target type {old['type']!r} != {inp.type!r}",
                                }
                            ]
                        },
                    )
                new_row = await conn.fetchrow(
                    """
                    INSERT INTO memories (
                        tenant_id, content, content_hash, embedding,
                        source_client_id, source_kind,
                        type, tags, metadata,
                        version, supersedes, is_current
                    ) VALUES ($1, $2, $3, $4::vector, $5, 'api', $6, $7, $8::jsonb, $9, $10, true)
                    RETURNING id, version, created_at
                    """,
                    ctx.tenant_id,
                    inp.content,
                    content_hash,
                    embed.vector,
                    ctx.client_id,
                    inp.type,
                    inp.tags,
                    json.dumps(inp.metadata),
                    int(old["version"]) + 1,
                    inp.supersedes,
                )
                # Mark old as superseded
                await conn.execute(
                    "UPDATE memories SET is_current = false, superseded_by = $1 WHERE id = $2",
                    new_row["id"],
                    inp.supersedes,
                )
                await ctx.deps.audit.audit(
                    conn,
                    action="memory.supersede",
                    result="success",
                    tenant_id=ctx.tenant_id,
                    identity_id=ctx.identity_id,
                    client_id=ctx.client_id,
                    target_id=new_row["id"],
                    target_kind="memory",
                    request_id=ctx.request_id,
                    details={
                        "old_id": str(inp.supersedes),
                        "embed_tokens": embed.input_tokens,
                    },
                )
                await ctx.deps.quotas.increment_write(ctx.tenant_id, embed.input_tokens)
                return MemoryWriteOutput(
                    id=new_row["id"],
                    version=int(new_row["version"]),
                    deduped=False,
                    merged_into=None,
                    created_at=new_row["created_at"],
                    request_id=ctx.request_id,
                )

            # Plain INSERT
            row = await conn.fetchrow(
                """
                INSERT INTO memories (
                    tenant_id, content, content_hash, embedding,
                    source_client_id, source_kind,
                    type, tags, metadata, version, is_current
                ) VALUES ($1, $2, $3, $4::vector, $5, 'api', $6, $7, $8::jsonb, 1, true)
                RETURNING id, version, created_at
                """,
                ctx.tenant_id,
                inp.content,
                content_hash,
                embed.vector,
                ctx.client_id,
                inp.type,
                inp.tags,
                json.dumps(inp.metadata),
            )
            await ctx.deps.audit.audit(
                conn,
                action="memory.write",
                result="success",
                tenant_id=ctx.tenant_id,
                identity_id=ctx.identity_id,
                client_id=ctx.client_id,
                target_id=row["id"],
                target_kind="memory",
                request_id=ctx.request_id,
                details={
                    "type": inp.type,
                    "tags": list(inp.tags),
                    "deduped": False,
                    "embed_tokens": embed.input_tokens,
                    "content_length": len(inp.content),
                },
            )
            await ctx.deps.quotas.increment_write(ctx.tenant_id, embed.input_tokens)
            return MemoryWriteOutput(
                id=row["id"],
                version=int(row["version"]),
                deduped=False,
                merged_into=None,
                created_at=row["created_at"],
                request_id=ctx.request_id,
            )
