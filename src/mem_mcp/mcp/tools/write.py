"""memory.write tool — store a memory (or merge into existing duplicate)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mem_mcp.db import system_tx, tenant_tx
from mem_mcp.embeddings.bedrock import EmbeddingError
from mem_mcp.embeddings.embed_or_skip import embed_or_skip
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.memory.dedupe import check_dup
from mem_mcp.memory.normalize import hash_content
from mem_mcp.memory.versioning import VERSIONED_TYPES
from mem_mcp.teams.references import (
    ReferenceTargetNotFoundError,
    insert_reference,
    resolve_reference_target,
)
from mem_mcp.teams.slugs import (
    SlugClueInvalidError,
    SlugClueReservedError,
    insert_slug_with_retry,
    redirect_slug_on_supersession,
)

_TAG_RE = re.compile(r"^[a-zA-Z0-9_:.-]+$")
MemoryType = Literal["note", "decision", "fact", "snippet", "question"]

# Hard write limit on memory content.
CONTENT_MAX_CHARS = 200_000

# Above this, we skip the Bedrock embed call entirely — Titan v2 caps at
# ~8K tokens (~32K chars) and would reject oversize input. NULL embedding
# is stored; semantic search drops those rows, keyword search still hits
# them via tsvector. See alembic 0011 (column nullability) and
# hybrid_query.py (IS NOT NULL guard on the semantic CTE).
EMBED_MAX_INPUT_CHARS = 32_000


class ReferenceInput(BaseModel):
    """One reference entry in the references[] array."""

    model_config = ConfigDict(extra="forbid")

    # Resolution: exactly one of the two shapes must be provided
    target_uuid: UUID | None = None
    target_team_id: UUID | None = None
    target_resource_type: Literal["decision", "fact"] | None = None
    target_slug: str | None = None
    # Metadata
    reference_kind: str = Field(..., min_length=1, max_length=128)
    target_fragment: int | None = None
    refs_version: Literal["pinned", "current"] = "pinned"

    @field_validator("target_slug", mode="after")
    @classmethod
    def _validate_slug(cls, v: str | None) -> str | None:
        if v is not None and not (1 <= len(v) <= 64):
            raise ValueError(f"target_slug must be 1-64 chars, got {len(v)}")
        return v


class MemoryWriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=CONTENT_MAX_CHARS)
    type: MemoryType = "note"
    tags: list[str] = Field(default_factory=list, max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict)
    supersedes: UUID | None = None
    parent_id: UUID | None = None
    force_new: bool = False
    expires_at: datetime | None = None
    ttl_seconds: int | None = None
    indexable: bool = True
    # Teams + slug support
    team_id: UUID | None = None
    visibility: Literal["team", "external", "public"] = "team"
    slug_clue: str | None = None
    references: list[ReferenceInput] | None = None
    fragment_id: int | None = None

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

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(v)) > 16_384:
            raise ValueError("metadata exceeds 16 KB serialized limit")
        return v

    @field_validator("supersedes", mode="after")
    @classmethod
    def _validate_supersedes(cls, v: UUID | None, info: Any) -> UUID | None:
        if v is not None:
            type_ = info.data.get("type", "note")
            if type_ not in VERSIONED_TYPES:
                raise ValueError(f"supersedes only valid for {VERSIONED_TYPES}, got {type_!r}")
        return v

    @field_validator("parent_id", mode="after")
    @classmethod
    def _validate_parent_id(cls, v: UUID | None, info: Any) -> UUID | None:
        if v is not None and info.data.get("supersedes") is not None:
            raise ValueError("parent_id and supersedes cannot be combined in the same call")
        return v

    @field_validator("ttl_seconds", mode="after")
    @classmethod
    def _validate_ttl_seconds(cls, v: int | None, info: Any) -> int | None:
        if v is not None:
            if not (1 <= v <= 31_536_000):  # 1 second to 1 year
                raise ValueError("ttl_seconds must be between 1 and 31536000 (1 year)")
        return v

    @field_validator("expires_at", "ttl_seconds", mode="after")
    @classmethod
    def _validate_expiry_conflict(cls, v: Any, info: Any) -> Any:
        # Check that both expires_at and ttl_seconds aren't set
        if info.field_name == "expires_at" and v is not None:
            if info.data.get("ttl_seconds") is not None:
                raise ValueError("only one of expires_at or ttl_seconds allowed")
            # Validate expires_at is not in the past
            if v <= datetime.now(UTC):
                raise ValueError("expires_at must be in the future")
        elif info.field_name == "ttl_seconds" and v is not None:
            if info.data.get("expires_at") is not None:
                raise ValueError("only one of expires_at or ttl_seconds allowed")
        return v

    @field_validator("slug_clue", mode="after")
    @classmethod
    def _validate_slug_clue(cls, v: str | None, info: Any) -> str | None:
        """slug_clue is REQUIRED for decision/fact, REJECTED for note/snippet/question."""
        type_ = info.data.get("type", "note")
        if type_ in VERSIONED_TYPES:  # decision, fact
            if not v:
                raise ValueError("slug_clue is required for decision/fact types")
        else:  # note, snippet, question
            if v:
                raise ValueError("slug_clue only valid for decision or fact types")
        return v

    @field_validator("references", mode="after")
    @classmethod
    def _validate_references(cls, v: list[ReferenceInput] | None) -> list[ReferenceInput] | None:
        """Each reference must have exactly one resolution path."""
        if v is None:
            return v
        for i, ref in enumerate(v):
            has_uuid = ref.target_uuid is not None
            has_slug = (
                ref.target_team_id is not None
                and ref.target_resource_type is not None
                and ref.target_slug is not None
            )
            if not (has_uuid or has_slug):
                raise ValueError(
                    f"references[{i}]: must provide either target_uuid OR "
                    "(target_team_id, target_resource_type, target_slug)"
                )
            if has_uuid and has_slug:
                raise ValueError(
                    f"references[{i}]: cannot provide both UUID and slug-based resolution"
                )
        return v


class MemoryWriteOutput(BaseModel):
    id: UUID
    version: int
    deduped: bool
    merged_into: UUID | None
    created_at: datetime
    request_id: str
    embedding_status: str


class MemoryWriteTool(BaseTool):
    """Store a memory (or merge into existing duplicate)."""

    name: ClassVar[str] = "memory_write"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memory_write"]
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

        # Compute expires_at if ttl_seconds provided
        expires_at: datetime | None = inp.expires_at
        if inp.ttl_seconds is not None:
            expires_at = datetime.now(UTC) + timedelta(seconds=inp.ttl_seconds)

        # Embed via Bedrock with graceful degrade on throttle/unavailable.
        # Returns (embedding_vec, tokens, embedding_status).
        # Note: invalid_input errors are now raised (no longer gracefully degraded).
        embedding_vec: list[float] | None
        embed_tokens: int
        embedding_status: str
        try:
            embedding_vec, embed_tokens, embedding_status = await embed_or_skip(
                inp.content,
                inp.indexable,
                ctx.deps.embeddings,
                memory_id_for_log=None,  # ID not yet generated
            )
        except EmbeddingError as exc:
            # Only invalid_input should reach here (other errors are gracefully degraded)
            raise JsonRpcError(
                -32000,
                "embedding validation failed",
                data={
                    "code": "embedding_validation_failed",
                    "kind": exc.code,
                    "message": str(exc)[:200],
                },
            ) from exc

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            # Resolve effective team_id (from caller's default_team_id if not provided)
            effective_team_id = inp.team_id
            if effective_team_id is None:
                effective_team_id = await conn.fetchval(
                    """
                    SELECT default_team_id FROM tenant_identities
                    WHERE tenant_id = $1 AND is_primary = true LIMIT 1
                    """,
                    ctx.tenant_id,
                )
            if effective_team_id is None:
                raise JsonRpcError(
                    -32602,
                    "no team_id and caller has no default_team_id; pass team_id explicitly",
                    data={
                        "errors": [
                            {
                                "path": "team_id",
                                "message": "team_id required but not provided and no default available",
                            }
                        ]
                    },
                )

            # Resolve and validate references FIRST (before memory INSERT).
            # On any miss or access error, reject the entire write.
            resolved_refs: list[dict[str, Any]] = []
            if inp.references:
                async with system_tx(ctx.db_pool) as sys_conn:
                    for i, ref in enumerate(inp.references):
                        try:
                            resolved = await resolve_reference_target(
                                sys_conn,
                                target_uuid=ref.target_uuid,
                                target_team_id=ref.target_team_id,
                                target_resource_type=ref.target_resource_type,
                                target_slug=ref.target_slug,
                                caller_user_id=ctx.tenant_id,
                            )
                            resolved_refs.append(
                                {
                                    "resolved_memory_id": resolved["memory_id"],
                                    "resolved_team_id": resolved["team_id"],
                                    "reference_kind": ref.reference_kind,
                                    "target_fragment": ref.target_fragment,
                                    "refs_version": ref.refs_version,
                                }
                            )
                        except ReferenceTargetNotFoundError as exc:
                            raise JsonRpcError(
                                -32602,
                                "reference target not found or not accessible",
                                data={
                                    "errors": [
                                        {
                                            "path": f"references[{i}]",
                                            "message": "reference target not found or not accessible",
                                        }
                                    ]
                                },
                            ) from exc

            # Dedupe (unless caller forced new or parent_id set)
            existing = None
            if not inp.force_new and inp.parent_id is None:
                existing = await check_dup(
                    conn,
                    ctx.tenant_id,
                    content_hash,
                    embedding_vec,
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
                        "embed_tokens": embed_tokens,
                    },
                )
                return MemoryWriteOutput(
                    id=existing.existing_id,
                    version=int(row["version"]),
                    deduped=True,
                    merged_into=existing.existing_id,
                    created_at=row["created_at"],
                    request_id=ctx.request_id,
                    embedding_status=embedding_status,
                )

            # parent_id branch: write a reply
            if inp.parent_id is not None:
                parent = await conn.fetchrow(
                    "SELECT id, tags, parent_id, deleted_at FROM memories WHERE id = $1 AND tenant_id = $2",
                    inp.parent_id,
                    ctx.tenant_id,
                )
                if parent is None:
                    raise JsonRpcError(
                        -32602,
                        "parent_id not found",
                        data={
                            "errors": [
                                {
                                    "path": "parent_id",
                                    "message": "parent memory not found in tenant scope",
                                }
                            ]
                        },
                    )
                if parent["deleted_at"] is not None:
                    raise JsonRpcError(
                        -32602,
                        "parent_id is soft-deleted",
                        data={
                            "errors": [
                                {
                                    "path": "parent_id",
                                    "message": "cannot reply to a deleted memory",
                                }
                            ]
                        },
                    )
                if parent["parent_id"] is not None:
                    raise JsonRpcError(
                        -32602,
                        "parent_id refers to a reply (flat hierarchy)",
                        data={
                            "errors": [
                                {
                                    "path": "parent_id",
                                    "message": "replies cannot have their own replies",
                                }
                            ]
                        },
                    )
                parent_tags = list(parent["tags"] or [])
                effective_tags = sorted(set(parent_tags) | set(inp.tags))
                if len(effective_tags) > 32:
                    raise JsonRpcError(
                        -32602,
                        "tag count exceeds 32 after parent inheritance",
                        data={
                            "errors": [
                                {
                                    "path": "tags",
                                    "message": f"effective tags = {len(effective_tags)} > 32",
                                }
                            ]
                        },
                    )
                reply_row = await conn.fetchrow(
                    """
                    INSERT INTO memories (
                        tenant_id, content, content_hash, embedding,
                        source_client_id, source_kind,
                        type, tags, metadata, version, is_current, parent_id,
                        expires_at, indexable, embedding_status, team_id, visibility, fragment_id
                    ) VALUES ($1, $2, $3, $4::vector, $5, 'api', $6, $7, $8::jsonb, 1, true, $9, $10, $11, $12, $13, $14, $15)
                    RETURNING id, version, created_at
                    """,
                    ctx.tenant_id,
                    inp.content,
                    content_hash,
                    embedding_vec,
                    ctx.client_id,
                    inp.type,
                    effective_tags,
                    json.dumps(inp.metadata),
                    inp.parent_id,
                    expires_at,
                    inp.indexable,
                    embedding_status,
                    effective_team_id,
                    inp.visibility,
                    inp.fragment_id,
                )
                # Insert references for reply
                for ref in resolved_refs:
                    await insert_reference(
                        conn,
                        source_memory_id=reply_row["id"],
                        target_memory_id=ref["resolved_memory_id"],
                        target_team_id=ref["resolved_team_id"],
                        reference_kind=ref["reference_kind"],
                        target_fragment=ref["target_fragment"],
                        refs_version=ref["refs_version"],
                    )
                await ctx.deps.audit.audit(
                    conn,
                    action="memory.write",
                    result="success",
                    tenant_id=ctx.tenant_id,
                    identity_id=ctx.identity_id,
                    client_id=ctx.client_id,
                    target_id=reply_row["id"],
                    target_kind="memory",
                    request_id=ctx.request_id,
                    details={
                        "parent_id": str(inp.parent_id),
                        "tags": effective_tags,
                        "inherited_tag_count": len(set(parent_tags) - set(inp.tags)),
                        "embed_tokens": embed_tokens,
                        "content_length": len(inp.content),
                        "type": inp.type,
                        "deduped": False,
                        "team_id": str(effective_team_id),
                        "visibility": inp.visibility,
                        "references_count": len(resolved_refs),
                    },
                )
                await ctx.deps.quotas.increment_write(ctx.tenant_id, embed_tokens)
                return MemoryWriteOutput(
                    id=reply_row["id"],
                    version=int(reply_row["version"]),
                    deduped=False,
                    merged_into=None,
                    created_at=reply_row["created_at"],
                    request_id=ctx.request_id,
                    embedding_status=embedding_status,
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
                        version, supersedes, is_current,
                        expires_at, indexable, embedding_status, team_id, visibility, fragment_id
                    ) VALUES ($1, $2, $3, $4::vector, $5, 'api', $6, $7, $8::jsonb, $9, $10, true, $11, $12, $13, $14, $15, $16)
                    RETURNING id, version, created_at
                    """,
                    ctx.tenant_id,
                    inp.content,
                    content_hash,
                    embedding_vec,
                    ctx.client_id,
                    inp.type,
                    inp.tags,
                    json.dumps(inp.metadata),
                    int(old["version"]) + 1,
                    inp.supersedes,
                    expires_at,
                    inp.indexable,
                    embedding_status,
                    effective_team_id,
                    inp.visibility,
                    inp.fragment_id,
                )
                # Mark old as superseded
                await conn.execute(
                    "UPDATE memories SET is_current = false, superseded_by = $1 WHERE id = $2 AND tenant_id = $3",
                    new_row["id"],
                    inp.supersedes,
                    ctx.tenant_id,
                )
                # Redirect slug from old to new (if a slug exists for old)
                await redirect_slug_on_supersession(
                    conn,
                    old_memory_id=inp.supersedes,
                    new_memory_id=new_row["id"],
                )
                # Insert references for new version
                for ref in resolved_refs:
                    await insert_reference(
                        conn,
                        source_memory_id=new_row["id"],
                        target_memory_id=ref["resolved_memory_id"],
                        target_team_id=ref["resolved_team_id"],
                        reference_kind=ref["reference_kind"],
                        target_fragment=ref["target_fragment"],
                        refs_version=ref["refs_version"],
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
                        "embed_tokens": embed_tokens,
                        "team_id": str(effective_team_id),
                        "visibility": inp.visibility,
                        "slug": None,  # slug redirected, not newly created
                        "references_count": len(resolved_refs),
                    },
                )
                await ctx.deps.quotas.increment_write(ctx.tenant_id, embed_tokens)
                return MemoryWriteOutput(
                    id=new_row["id"],
                    version=int(new_row["version"]),
                    deduped=False,
                    merged_into=None,
                    created_at=new_row["created_at"],
                    request_id=ctx.request_id,
                    embedding_status=embedding_status,
                )

            # Plain INSERT
            row = await conn.fetchrow(
                """
                INSERT INTO memories (
                    tenant_id, content, content_hash, embedding,
                    source_client_id, source_kind,
                    type, tags, metadata, version, is_current,
                    expires_at, indexable, embedding_status, team_id, visibility, fragment_id
                ) VALUES ($1, $2, $3, $4::vector, $5, 'api', $6, $7, $8::jsonb, 1, true, $9, $10, $11, $12, $13, $14)
                RETURNING id, version, created_at
                """,
                ctx.tenant_id,
                inp.content,
                content_hash,
                embedding_vec,
                ctx.client_id,
                inp.type,
                inp.tags,
                json.dumps(inp.metadata),
                expires_at,
                inp.indexable,
                embedding_status,
                effective_team_id,
                inp.visibility,
                inp.fragment_id,
            )

            # Insert slug for decision/fact types
            actual_slug: str | None = None
            if inp.type in VERSIONED_TYPES and inp.slug_clue:
                try:
                    title = inp.content[:64]
                    actual_slug = await insert_slug_with_retry(
                        conn,
                        team_id=effective_team_id,
                        resource_type=inp.type,  # type: ignore[arg-type]
                        clue=inp.slug_clue,
                        memory_id=row["id"],
                        title=title,
                    )
                except (SlugClueInvalidError, SlugClueReservedError) as exc:
                    raise JsonRpcError(
                        -32602,
                        str(exc),
                        data={
                            "errors": [
                                {
                                    "path": "slug_clue",
                                    "message": str(exc),
                                }
                            ]
                        },
                    ) from exc

            # Insert references
            for ref in resolved_refs:
                await insert_reference(
                    conn,
                    source_memory_id=row["id"],
                    target_memory_id=ref["resolved_memory_id"],
                    target_team_id=ref["resolved_team_id"],
                    reference_kind=ref["reference_kind"],
                    target_fragment=ref["target_fragment"],
                    refs_version=ref["refs_version"],
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
                    "embed_tokens": embed_tokens,
                    "content_length": len(inp.content),
                    "team_id": str(effective_team_id),
                    "visibility": inp.visibility,
                    "slug": actual_slug,
                    "references_count": len(resolved_refs),
                },
            )
            await ctx.deps.quotas.increment_write(ctx.tenant_id, embed_tokens)
            return MemoryWriteOutput(
                id=row["id"],
                version=int(row["version"]),
                deduped=False,
                merged_into=None,
                created_at=row["created_at"],
                request_id=ctx.request_id,
                embedding_status=embedding_status,
            )
