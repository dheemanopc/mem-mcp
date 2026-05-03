"""memory.update tool — in-place update or versioned change (T-7.2)."""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.memory.normalize import hash_content
from mem_mcp.memory.versioning import VERSIONED_TYPES

_TAG_RE = re.compile(r"^[a-zA-Z0-9_:.-]+$")
MemoryType = Literal["note", "decision", "fact", "snippet", "question"]


class MemoryUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    content: str | None = None
    type: Literal["note", "decision", "fact", "snippet", "question"] | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    tags_op: Literal["replace", "add", "remove"] = "replace"

    @field_validator("content", mode="after")
    @classmethod
    def _validate_content(cls, v: str | None) -> str | None:
        if v is not None and not (1 <= len(v) <= 32_768):
            raise ValueError("content length out of range: must be 1..32768")
        return v

    @field_validator("tags", mode="after")
    @classmethod
    def _validate_tags(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        if len(v) > 32:
            raise ValueError("too many tags (max 32)")
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


class MemoryUpdateOutput(BaseModel):
    id: UUID
    version: int
    is_new_version: bool
    tags: list[str] | None
    request_id: str


class MemoryUpdateTool(BaseTool):
    """Update a memory in-place or create a new version (T-7.2)."""

    name: ClassVar[str] = "memory.update"
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = MemoryUpdateInput
    OutputModel: ClassVar[type[BaseModel]] = MemoryUpdateOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemoryUpdateInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            # Initial lookup
            target = await conn.fetchrow(
                """
                SELECT id, type, content, version, tags, metadata, deleted_at
                FROM memories
                WHERE id = $1 AND tenant_id = $2
                """,
                inp.id,
                ctx.tenant_id,
            )
            if target is None:
                raise JsonRpcError(
                    -32602,
                    "memory not found",
                    data={"errors": [{"path": "id", "message": "not found in tenant scope"}]},
                )
            if target["deleted_at"] is not None:
                raise JsonRpcError(
                    -32602,
                    "cannot update deleted memory",
                    data={"errors": [{"path": "id", "message": "memory is deleted"}]},
                )

            # Decide path
            content_changed = inp.content is not None and inp.content != target["content"]
            type_changed = inp.type is not None and inp.type != target["type"]
            target_was_versioned = target["type"] in VERSIONED_TYPES
            new_type = inp.type or target["type"]
            new_is_versioned = new_type in VERSIONED_TYPES

            # Check if we need new version: either (versioned and content changed) or (type change to versioned)
            new_version_path = ((target_was_versioned or new_is_versioned) and content_changed) or (
                type_changed and new_is_versioned and not target_was_versioned
            )

            if new_version_path:
                # NEW VERSION path
                if target_was_versioned:
                    new_version_number = int(target["version"]) + 1
                else:
                    # Type promotion from non-versioned to versioned
                    new_version_number = 1

                # Determine content for embedding
                new_content = inp.content if inp.content is not None else target["content"]

                # Check quota before embedding
                await ctx.deps.quotas.check_write(ctx.tenant_id, len(new_content))

                # Re-embed
                embed = await ctx.deps.embeddings.embed(new_content)

                # Compute hash
                content_hash = hash_content(new_content)

                # Compute final tags
                final_tags = self._compute_tags(target["tags"], inp.tags, inp.tags_op)

                # INSERT new row
                new_row = await conn.fetchrow(
                    """
                    INSERT INTO memories (
                        tenant_id, content, content_hash, embedding,
                        source_client_id, source_kind,
                        type, tags, metadata,
                        version, supersedes, is_current
                    ) VALUES ($1, $2, $3, $4::vector, $5, 'api', $6, $7, $8::jsonb, $9, $10, true)
                    RETURNING id, version
                    """,
                    ctx.tenant_id,
                    new_content,
                    content_hash,
                    embed.vector,
                    ctx.client_id,
                    new_type,
                    final_tags,
                    json.dumps(inp.metadata) if inp.metadata is not None else "{}",
                    new_version_number,
                    target["id"],
                )

                # UPDATE old row: mark as superseded
                await conn.execute(
                    """
                    UPDATE memories
                    SET is_current = false, superseded_by = $1
                    WHERE id = $2 AND tenant_id = $3
                    """,
                    new_row["id"],
                    target["id"],
                    ctx.tenant_id,
                )

                # Audit
                await ctx.deps.audit.audit(
                    conn,
                    action="memory.update",
                    result="success",
                    tenant_id=ctx.tenant_id,
                    identity_id=ctx.identity_id,
                    client_id=ctx.client_id,
                    target_id=new_row["id"],
                    target_kind="memory",
                    request_id=ctx.request_id,
                    details={
                        "is_new_version": True,
                        "content_changed": content_changed,
                        "type_changed": type_changed,
                    },
                )

                # Quota
                await ctx.deps.quotas.increment_write(ctx.tenant_id, embed.input_tokens)

                return MemoryUpdateOutput(
                    id=new_row["id"],
                    version=int(new_row["version"]),
                    is_new_version=True,
                    tags=final_tags,
                    request_id=ctx.request_id,
                )

            else:
                # IN-PLACE path
                # Compute final tags
                final_tags = self._compute_tags(target["tags"], inp.tags, inp.tags_op)

                # Determine new metadata
                new_metadata = inp.metadata if inp.metadata is not None else target["metadata"]

                # Only re-embed if content changed
                embed_tokens = 0
                if content_changed and inp.content is not None:
                    # Quota check
                    await ctx.deps.quotas.check_write(ctx.tenant_id, len(inp.content))

                    # Re-embed
                    embed = await ctx.deps.embeddings.embed(inp.content)
                    embed_tokens = embed.input_tokens

                    # Compute hash
                    content_hash = hash_content(inp.content)

                    # UPDATE with content change
                    await conn.fetchval(
                        """
                        UPDATE memories
                        SET content = $1, content_hash = $2, embedding = $3::vector,
                            type = $4, tags = $5, metadata = $6::jsonb, updated_at = now()
                        WHERE id = $7 AND tenant_id = $8
                        RETURNING updated_at
                        """,
                        inp.content,
                        content_hash,
                        embed.vector,
                        new_type,
                        final_tags,
                        json.dumps(new_metadata),
                        target["id"],
                        ctx.tenant_id,
                    )
                else:
                    # UPDATE without content change (no embedding)
                    await conn.fetchval(
                        """
                        UPDATE memories
                        SET type = $1, tags = $2, metadata = $3::jsonb, updated_at = now()
                        WHERE id = $4 AND tenant_id = $5
                        RETURNING updated_at
                        """,
                        new_type,
                        final_tags,
                        json.dumps(new_metadata),
                        target["id"],
                        ctx.tenant_id,
                    )

                # Audit
                await ctx.deps.audit.audit(
                    conn,
                    action="memory.update",
                    result="success",
                    tenant_id=ctx.tenant_id,
                    identity_id=ctx.identity_id,
                    client_id=ctx.client_id,
                    target_id=target["id"],
                    target_kind="memory",
                    request_id=ctx.request_id,
                    details={
                        "is_new_version": False,
                        "content_changed": content_changed,
                        "type_changed": type_changed,
                    },
                )

                # Quota (only if actually embedded)
                if embed_tokens > 0:
                    await ctx.deps.quotas.increment_write(ctx.tenant_id, embed_tokens)

                return MemoryUpdateOutput(
                    id=target["id"],
                    version=int(target.get("version") or 1),
                    is_new_version=False,
                    tags=final_tags,
                    request_id=ctx.request_id,
                )

    def _compute_tags(
        self,
        existing_tags: list[str],
        new_tags: list[str] | None,
        tags_op: Literal["replace", "add", "remove"],
    ) -> list[str]:
        """Compute final tags based on tags_op."""
        if tags_op == "replace":
            return new_tags if new_tags is not None else list(existing_tags)
        elif tags_op == "add":
            # Union: keep existing, then add new in order, dedup
            result = list(existing_tags)
            seen = set(existing_tags)
            if new_tags:
                for tag in new_tags:
                    if tag not in seen:
                        result.append(tag)
                        seen.add(tag)
            return result
        else:  # tags_op == "remove"
            # Remove specified tags
            remove_set = set(new_tags or [])
            return [t for t in existing_tags if t not in remove_set]
