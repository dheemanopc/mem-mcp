"""memory.list tool — paginated listing with cursor-based pagination."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from mem_mcp.db import tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.memory.hybrid_query import (
    HEADLINE_OPTIONS,
    HEADLINE_SOURCE_MAX_CHARS,
    LENIENT_TSQUERY_SQL,
    PREVIEW_FALLBACK_CHARS,
    PREVIEW_MAX_CHARS,
)

MemoryType = Literal["note", "decision", "fact", "snippet", "question"]


class MemoryListInput(BaseModel):
    """Input for memory.list tool."""

    model_config = ConfigDict(extra="forbid")

    # Lenient textual filter: rows must keyword-match at least one word
    # (English-stemmed, OR semantics). Pagination order is unchanged
    # (created_at/updated_at) — this filters, it does not rank.
    keywords: str | None = Field(default=None, min_length=1, max_length=512)
    tags: list[str] | None = None
    type: MemoryType | None = None
    since: datetime | None = None
    until: datetime | None = None
    # F-7 fold + SDK-parity additions (DA `336346ef` §1) — filter params for
    # team scope, indexability, and thread parent. Each is a strict additional
    # AND on the WHERE clause; RLS policy (memories_select) still gates first.
    team_id: UUID | None = None
    indexable: bool | None = None
    parent_id: UUID | None = None
    include_deleted: bool = False
    include_expired: bool = False
    include_history: bool = False
    order_by: Literal["created_at", "updated_at"] = "created_at"
    order: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = None


class MemoryListItem(BaseModel):
    """Single memory in list results."""

    id: UUID
    # Truncated to 2000 chars; full body via memory_get.
    content: str
    # Keyword-windowed snippet (**match** markers) when `keywords` was
    # passed; head-of-content otherwise.
    preview: str
    type: str
    tags: list[str]
    version: int
    is_current: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    expires_at: datetime | None
    indexable: bool
    embedding_status: str


class MemoryListOutput(BaseModel):
    """Output for memory.list tool."""

    results: list[MemoryListItem]
    next_cursor: str | None
    request_id: str


def _encode_cursor(order_by_value: datetime | None, id_: UUID) -> str:
    """Encode a cursor from (order_by_value, id) tuple."""
    if order_by_value is None:
        raise ValueError("order_by_value cannot be None")
    value_iso = order_by_value.isoformat()
    return base64.urlsafe_b64encode(json.dumps([value_iso, str(id_)]).encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, str]:
    """Decode a cursor back to (order_by_value_iso, id_str)."""
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return data[0], data[1]
    except (json.JSONDecodeError, IndexError, ValueError) as exc:
        raise ValueError(f"Invalid cursor format: {exc}") from exc


class MemoryListTool(BaseTool):
    """List memories with pagination, filtering, and sorting."""

    name: ClassVar[str] = "memory_list"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memory_list"]
    required_scope: ClassVar[str] = "memory.read"
    InputModel: ClassVar[type[BaseModel]] = MemoryListInput
    OutputModel: ClassVar[type[BaseModel]] = MemoryListOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemoryListInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        # Parse cursor if provided
        cursor_order_value_iso: str | None = None
        cursor_id: str | None = None
        if inp.cursor:
            try:
                cursor_order_value_iso, cursor_id = _decode_cursor(inp.cursor)
            except ValueError as exc:
                raise JsonRpcError(
                    -32602,
                    "invalid cursor",
                    data={"errors": [{"path": "cursor", "message": str(exc)}]},
                ) from exc

        # Build WHERE clauses dynamically
        where_clauses: list[str] = [
            "tenant_id = $1",
        ]
        params: list[Any] = [ctx.tenant_id]

        if not inp.include_deleted:
            where_clauses.append("deleted_at IS NULL")

        if not inp.include_expired:
            where_clauses.append("(expires_at IS NULL OR expires_at > NOW())")

        if not inp.include_history:
            where_clauses.append("is_current = true")

        if inp.type:
            where_clauses.append(f"type = ${len(params) + 1}")
            params.append(inp.type)

        if inp.tags:
            where_clauses.append(f"tags @> ${len(params) + 1}")
            params.append(inp.tags)

        if inp.since:
            where_clauses.append(f"created_at >= ${len(params) + 1}")
            params.append(inp.since)

        if inp.until:
            where_clauses.append(f"created_at <= ${len(params) + 1}")
            params.append(inp.until)

        # F-7 fold + SDK-parity additions per DA `336346ef` §1. RLS-safe: each
        # is a strict additional AND that can only narrow what the
        # memories_select policy already permits; cannot widen access.
        if inp.team_id is not None:
            where_clauses.append(f"team_id = ${len(params) + 1}")
            params.append(inp.team_id)

        if inp.indexable is not None:
            where_clauses.append(f"indexable = ${len(params) + 1}")
            params.append(inp.indexable)

        if inp.parent_id is not None:
            where_clauses.append(f"parent_id = ${len(params) + 1}")
            params.append(inp.parent_id)

        # Lenient keyword filter (OR-of-lexemes tsquery; see hybrid_query).
        # The same parameter is referenced by the preview headline below.
        # NULL tsquery (all-stopword input) matches nothing — explicit and
        # cheap rather than silently returning everything.
        preview_select = f"LEFT(content, {PREVIEW_FALLBACK_CHARS}) AS preview"
        if inp.keywords:
            tsq = LENIENT_TSQUERY_SQL.format(param=f"${len(params) + 1}")
            where_clauses.append(f"content_tsv @@ {tsq}")
            preview_select = (
                f"LEFT(COALESCE(ts_headline('english', LEFT(content, {HEADLINE_SOURCE_MAX_CHARS}), "
                f"{tsq}, '{HEADLINE_OPTIONS}'), LEFT(content, {PREVIEW_FALLBACK_CHARS})), "
                f"{PREVIEW_MAX_CHARS}) AS preview"
            )
            params.append(inp.keywords)

        # Keyset pagination: build cursor predicate
        if cursor_order_value_iso and cursor_id:
            order_col = inp.order_by
            direction_op = "<" if inp.order == "desc" else ">"
            where_clauses.append(
                f"({order_col}, id) {direction_op} (${len(params) + 1}, ${len(params) + 2}::uuid)"
            )
            params.append(cursor_order_value_iso)
            params.append(cursor_id)

        where_str = " AND ".join(f"({c})" for c in where_clauses)

        # Build ORDER BY
        if inp.order_by == "updated_at":
            order_clause = f"updated_at {inp.order.upper()}, id"
        else:
            order_clause = f"created_at {inp.order.upper()}, id"

        # Fetch limit + 1 to detect if there's a next page
        fetch_limit = inp.limit + 1
        query = f"""
            SELECT id, LEFT(content, 2000) AS content, {preview_select}, type, tags, version, is_current, created_at, updated_at, deleted_at,
                   expires_at, indexable, embedding_status
            FROM memories
            WHERE {where_str}
            ORDER BY {order_clause}
            LIMIT ${len(params) + 1}
        """

        params.append(fetch_limit)

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            rows = await conn.fetch(query, *params)

            # Determine next_cursor
            next_cursor: str | None = None
            if len(rows) > inp.limit:
                # We got an extra row, so there's a next page
                last_row = rows[inp.limit]  # The (limit+1)th row
                if inp.order_by == "updated_at":
                    order_value = last_row["updated_at"]
                else:
                    order_value = last_row["created_at"]
                next_cursor = _encode_cursor(order_value, last_row["id"])
                rows = rows[: inp.limit]  # Drop the overflow row

            # Audit
            await ctx.deps.audit.audit(
                conn,
                action="memory.list",
                result="success",
                tenant_id=ctx.tenant_id,
                identity_id=ctx.identity_id,
                client_id=ctx.client_id,
                request_id=ctx.request_id,
                details={
                    "result_count": len(rows),
                    "has_next": next_cursor is not None,
                    "filters_count": len([c for c in where_clauses if c != "tenant_id = $1"]),
                },
            )

        items = [MemoryListItem(**dict(r)) for r in rows]
        return MemoryListOutput(
            results=items,
            next_cursor=next_cursor,
            request_id=ctx.request_id,
        )
