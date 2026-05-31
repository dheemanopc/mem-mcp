"""memory_get_batch tool — fetch N specific memories in one round-trip.

Per spec memsys:968ff70e (ratified via memsys:26610ba6 with worker proposal
memsys:10171002). Pure round-trip optimization; no new content semantics.

Entries are independently access-checked via RLS (UUID path: tenant_tx)
and via user_effective_team_access (slug path: cross-tenant by design,
same pattern as memsys_slug_lookup from Spec 2). Missing rows synthesize
an opaque "memory not found" error per the IT-08 contract (Spec 4
ratification memsys:70b43c08) so callers cannot distinguish not-found
from no-access across team boundaries.
"""

from __future__ import annotations

from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mem_mcp.db import system_tx, tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.mcp.tools.get import MemoryRecord
from mem_mcp.teams.slugs import lookup_slug

_NOT_ACCESSIBLE_CODE = "memory_not_accessible"
_NOT_ACCESSIBLE_MSG = "memory not found or not accessible"


class MemoryGetBatchEntry(BaseModel):
    """One entry in the batch — either id-shape OR slug-tuple shape."""

    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    team_id: UUID | None = None
    resource_type: Literal["decision", "fact"] | None = None
    slug: str | None = None

    @model_validator(mode="after")
    def _exactly_one_shape(self) -> MemoryGetBatchEntry:
        has_id = self.id is not None
        has_slug_tuple = (
            self.team_id is not None and self.resource_type is not None and self.slug is not None
        )
        if has_id and has_slug_tuple:
            raise ValueError("cannot provide both id and (team_id, resource_type, slug)")
        if not has_id and not has_slug_tuple:
            raise ValueError("must provide either id OR (team_id, resource_type, slug)")
        if self.slug is not None and not (1 <= len(self.slug) <= 64):
            raise ValueError(f"slug must be 1-64 chars, got {len(self.slug)}")
        return self


class MemoryGetBatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: list[MemoryGetBatchEntry] = Field(..., min_length=1, max_length=50)


class MemoryGetBatchResultEntry(BaseModel):
    ok: bool
    memory: MemoryRecord | None = None
    error: dict[str, str] | None = None

    @field_validator("error", mode="after")
    @classmethod
    def _no_extra_fields(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is not None:
            allowed = {"code", "message"}
            extra = set(v.keys()) - allowed
            if extra:
                raise ValueError(f"error dict has unexpected keys: {extra}")
        return v


class MemoryGetBatchOutput(BaseModel):
    results: list[MemoryGetBatchResultEntry]


class MemoryGetBatchTool(BaseTool):
    """Fetch N specific memories by UUID or (team_id, resource_type, slug)."""

    name: ClassVar[str] = "memory_get_batch"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memory_get_batch"]
    required_scope: ClassVar[str] = "memory.read"
    InputModel: ClassVar[type[BaseModel]] = MemoryGetBatchInput
    OutputModel: ClassVar[type[BaseModel]] = MemoryGetBatchOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemoryGetBatchInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        # Partition: UUID-shape requests vs slug-shape requests; preserve original index.
        uuid_lookups: list[tuple[int, UUID]] = []
        slug_lookups: list[tuple[int, UUID, Literal["decision", "fact"], str]] = []
        for i, entry in enumerate(inp.requests):
            if entry.id is not None:
                uuid_lookups.append((i, entry.id))
            else:
                # model_validator enforces all three are set when id is None.
                assert entry.team_id is not None
                assert entry.resource_type is not None
                assert entry.slug is not None
                slug_lookups.append((i, entry.team_id, entry.resource_type, entry.slug))

        # uuid_results: original_index -> MemoryRecord (only present if accessible)
        uuid_results: dict[int, MemoryRecord] = {}
        # slug_results: original_index -> MemoryRecord
        slug_results: dict[int, MemoryRecord] = {}

        # UUID path: tenant_tx (RLS scopes by tenant_id automatically).
        if uuid_lookups:
            async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
                uuid_ids = [u for (_, u) in uuid_lookups]
                rows = await conn.fetch(
                    """
                    SELECT id, content, type, tags, metadata, version, is_current,
                           supersedes, superseded_by, created_at, updated_at,
                           deleted_at, expires_at, indexable, embedding_status
                    FROM memories
                    WHERE id = ANY($1::uuid[])
                      AND tenant_id = $2
                      AND deleted_at IS NULL
                    """,
                    uuid_ids,
                    ctx.tenant_id,
                )
                by_id = {row["id"]: row for row in rows}
                for orig_idx, requested_id in uuid_lookups:
                    row = by_id.get(requested_id)
                    if row is not None:
                        uuid_results[orig_idx] = MemoryRecord(**dict(row))

        # Slug path: lookup_slug is cross-tenant by design (Spec 2). The access
        # check + slug-lookup queries hit RLS-free tables (user_effective_team_access,
        # slugs) so run under system_tx. The memories fetch MUST run under tenant_tx
        # — memories has an RLS policy that bare-casts the app.current_tenant_id
        # GUC to UUID. Under system_tx the GUC is NULL on fresh conns and '' on
        # conns recycled from a rolled-back tenant_tx (PG custom-GUC quirk; see
        # migration 0031 for the same cluster on async_write_queue). The cast
        # crashes on '', and returns 0 rows on NULL. Tenant_tx sets the GUC to a
        # real UUID and lets the policy's tenant-scope + visibility='team' clauses
        # fire correctly — same semantic as sync memory_get.
        if slug_lookups:
            # First pass under system_tx: access-check + slug→memory_id resolution.
            resolved: dict[int, UUID] = {}
            async with system_tx(ctx.db_pool) as conn:
                for orig_idx, team_id, resource_type, slug in slug_lookups:
                    access = await conn.fetchval(
                        """
                        SELECT 1 FROM user_effective_team_access
                         WHERE user_id = $1 AND resource_team_id = $2
                        """,
                        ctx.tenant_id,
                        team_id,
                    )
                    if access is None:
                        continue  # synthesized error below
                    slug_row = await lookup_slug(
                        conn,
                        team_id=team_id,
                        resource_type=resource_type,
                        slug=slug,
                    )
                    if slug_row is None:
                        continue
                    resolved[orig_idx] = slug_row["memory_id"]  # type: ignore[assignment]

            # Second pass under tenant_tx: fetch memory rows. The memories RLS
            # policy fires against the caller's tenant GUC and matches both
            # caller-tenant rows and cross-team rows with visibility='team'.
            if resolved:
                async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
                    mem_ids = list(resolved.values())
                    rows = await conn.fetch(
                        """
                        SELECT id, content, type, tags, metadata, version, is_current,
                               supersedes, superseded_by, created_at, updated_at,
                               deleted_at, expires_at, indexable, embedding_status
                        FROM memories
                        WHERE id = ANY($1::uuid[]) AND deleted_at IS NULL
                        """,
                        mem_ids,
                    )
                    by_id = {row["id"]: row for row in rows}
                    for orig_idx, mem_id in resolved.items():
                        row = by_id.get(mem_id)
                        if row is not None:
                            slug_results[orig_idx] = MemoryRecord(**dict(row))

        # Walk original requests; assemble results in submission order.
        results: list[MemoryGetBatchResultEntry] = []
        found_count = 0
        for i in range(len(inp.requests)):
            mem = uuid_results.get(i) or slug_results.get(i)
            if mem is not None:
                found_count += 1
                results.append(MemoryGetBatchResultEntry(ok=True, memory=mem))
            else:
                results.append(
                    MemoryGetBatchResultEntry(
                        ok=False,
                        error={"code": _NOT_ACCESSIBLE_CODE, "message": _NOT_ACCESSIBLE_MSG},
                    )
                )

        # Single audit row for the batch — one quota tick.
        async with system_tx(ctx.db_pool) as conn:
            await ctx.deps.audit.audit(
                conn,
                action="memory.get_batch",
                result="success",
                tenant_id=ctx.tenant_id,
                identity_id=ctx.identity_id,
                client_id=ctx.client_id,
                target_id=None,
                target_kind="memory",
                request_id=ctx.request_id,
                details={
                    "request_count": len(inp.requests),
                    "found_count": found_count,
                    "uuid_count": len(uuid_lookups),
                    "slug_count": len(slug_lookups),
                },
            )

        return MemoryGetBatchOutput(results=results)
