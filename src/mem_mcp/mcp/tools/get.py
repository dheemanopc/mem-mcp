"""memory.get tool — fetch one memory + optional history."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from mem_mcp.db import system_tx, tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.memory.access_tracking import bump_access
from mem_mcp.teams.slugs import lookup_slug


class MemoryGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    include_history: bool = False
    # Slug-based lookup (alternative to id)
    team_id: UUID | None = None
    resource_type: Literal["decision", "fact"] | None = None
    slug: str | None = None

    @field_validator("id", "team_id", "resource_type", "slug", mode="after")
    @classmethod
    def _validate_lookup_path(cls, v: Any, info: Any) -> Any:
        """Either id alone, OR all three of (team_id, resource_type, slug)."""
        data = info.data
        has_id = data.get("id") is not None
        has_slug_tuple = (
            data.get("team_id") is not None
            and data.get("resource_type") is not None
            and data.get("slug") is not None
        )

        if info.field_name == "id":
            if has_id and has_slug_tuple:
                raise ValueError("cannot provide both id and (team_id, resource_type, slug)")
            if not has_id and not has_slug_tuple:
                raise ValueError("must provide either id OR (team_id, resource_type, slug)")
        elif info.field_name in ("team_id", "resource_type", "slug"):
            # Already checked above; just validate this specific field
            if info.field_name == "slug" and v is not None and not (1 <= len(v) <= 64):
                raise ValueError(f"slug must be 1-64 chars, got {len(v)}")

        return v


class MemoryRecord(BaseModel):
    id: UUID
    content: str
    type: str
    tags: list[str]
    metadata: dict[str, Any]
    version: int
    is_current: bool
    supersedes: UUID | None
    superseded_by: UUID | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    expires_at: datetime | None
    indexable: bool
    embedding_status: str

    @field_validator("metadata", mode="before")
    @classmethod
    def _parse_jsonb(cls, v: Any) -> Any:
        # asyncpg returns the JSONB column as a raw JSON str when no codec is
        # registered for it on the active connection. Connection codecs are
        # registered via the pool init (db/pool.py) but the registration only
        # applies reliably to *literal* casts; column-typed JSONB reads on
        # already-prepared statements still return str on asyncpg 0.30.
        # Accept both shapes; parse str to dict on the way in.
        if isinstance(v, str):
            return json.loads(v) if v else {}
        return v


class MemoryGetOutput(BaseModel):
    memory: MemoryRecord
    history: list[MemoryRecord]


class MemoryGetTool(BaseTool):
    """Fetch one memory by id (current version + history if requested)."""

    name: ClassVar[str] = "memory_get"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memory_get"]
    required_scope: ClassVar[str] = "memory.read"
    InputModel: ClassVar[type[BaseModel]] = MemoryGetInput
    OutputModel: ClassVar[type[BaseModel]] = MemoryGetOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemoryGetInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        # Resolve memory_id from slug if needed
        lookup_id = inp.id
        if lookup_id is None:
            # Slug-based lookup — must check access first
            async with system_tx(ctx.db_pool) as sys_conn:
                access = await sys_conn.fetchval(
                    """
                    SELECT 1 FROM user_effective_team_access
                     WHERE user_id = $1 AND resource_team_id = $2
                    """,
                    ctx.tenant_id,
                    inp.team_id,
                )
                if access is None:
                    # No access — opaque 404
                    raise JsonRpcError(
                        -32602,
                        "memory not found via slug",
                        data={
                            "errors": [
                                {
                                    "path": "slug",
                                    "message": "not found or not accessible",
                                }
                            ]
                        },
                    )
                # Access granted — lookup
                slug_row = await lookup_slug(
                    sys_conn,
                    team_id=inp.team_id,  # type: ignore[arg-type]
                    resource_type=inp.resource_type,  # type: ignore[arg-type]
                    slug=inp.slug,  # type: ignore[arg-type]
                )
                if slug_row is None:
                    raise JsonRpcError(
                        -32602,
                        "memory not found via slug",
                        data={
                            "errors": [
                                {
                                    "path": "slug",
                                    "message": "not found or not accessible",
                                }
                            ]
                        },
                    )
                lookup_id = slug_row["memory_id"]

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT id, content, type, tags, metadata, version, is_current,
                       supersedes, superseded_by, created_at, updated_at, deleted_at,
                       expires_at, indexable, embedding_status
                FROM memories
                WHERE id = $1 AND tenant_id = $2
                """,
                lookup_id,
                ctx.tenant_id,
            )
            if row is None:
                raise JsonRpcError(
                    -32602,
                    "memory not found",
                    data={
                        "errors": [
                            {
                                "path": "id",
                                "message": "not found in tenant scope",
                            }
                        ]
                    },
                )

            history: list[dict[str, Any]] = []
            if inp.include_history:
                # Walk supersedes chain backwards
                hrows = await conn.fetch(
                    """
                    WITH RECURSIVE chain AS (
                        SELECT id, content, type, tags, metadata, version, is_current,
                               supersedes, superseded_by, created_at, updated_at, deleted_at,
                               expires_at, indexable, embedding_status
                        FROM memories
                        WHERE id = $1 AND tenant_id = $2
                        UNION ALL
                        SELECT m.id, m.content, m.type, m.tags, m.metadata, m.version,
                               m.is_current, m.supersedes, m.superseded_by,
                               m.created_at, m.updated_at, m.deleted_at,
                               m.expires_at, m.indexable, m.embedding_status
                        FROM memories m JOIN chain c ON m.id = c.supersedes
                        WHERE m.tenant_id = $2
                    )
                    SELECT * FROM chain WHERE id <> $1
                    ORDER BY version DESC
                    """,
                    lookup_id,
                    ctx.tenant_id,
                )
                history = [dict(h) for h in hrows]

            await ctx.deps.audit.audit(
                conn,
                action="memory.get",
                result="success",
                tenant_id=ctx.tenant_id,
                identity_id=ctx.identity_id,
                client_id=ctx.client_id,
                target_id=lookup_id,
                target_kind="memory",
                request_id=ctx.request_id,
                details={
                    "include_history": inp.include_history,
                    "history_count": len(history),
                },
            )

        # Bump access-time so the stale-detection UI can find untouched
        # memories. Fire-and-forget; throttled at 5min per row.
        await bump_access(ctx.db_pool, ctx.tenant_id, [lookup_id])

        return MemoryGetOutput(
            memory=MemoryRecord(**dict(row)),
            history=[MemoryRecord(**h) for h in history],
        )
