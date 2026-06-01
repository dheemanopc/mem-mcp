"""memory_write_async tool — fire-and-forget write with bounded eventual consistency.

Per spec memsys:f28c4dab ratified via memsys:26610ba6 with worker proposal
memsys:80b04691.

Submit-time validation: shape (Pydantic, reused from memory_write), quota
(rejects sync on exhaustion), bare reference-existence (rejects sync if a
referenced UUID/slug doesn't exist). Deferred to drain: embedding,
full reference access-check, dedup, slug allocation, parent/supersedes
validation.

Returns `{request_id, queued_at, estimated_consistency_by}` immediately.
The actual memory persistence happens via mem_mcp.jobs.async_write_drain
(systemd daemon mem-mcp-async-write-drain.service, blessed Decision 2
in proposal — Type=simple rather than 1s oneshot timer).

Read-your-own-writes is NOT guaranteed in the same session — documented
limitation; PMO prompts will use sync memory_write when read-after-write
is needed. Per-tenant ordering IS preserved end-to-end via the drain's
ORDER BY (tenant_id, submitted_at) and the created_at = submitted_at
carry-through (blessed Decision 1).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import ClassVar
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from mem_mcp.db import system_tx, tenant_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.mcp.tools.write import MemoryWriteInput

# Owner-blessed: spec advertises a fixed 5s eventual-consistency window for v1.
# Dynamic estimation from queue depth is a follow-up (needs telemetry that
# doesn't exist yet) — Decision 4 in proposal memsys:80b04691.
ESTIMATED_CONSISTENCY_SECONDS = 5


class MemoryWriteAsyncOutput(BaseModel):
    """Submission ack — actual persistence happens asynchronously via drain."""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    queued_at: datetime
    estimated_consistency_by: datetime


class MemoryWriteAsyncTool(BaseTool):
    """Fire-and-forget memory write. Same input as memory_write."""

    name: ClassVar[str] = "memory_write_async"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memory_write_async"]
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = MemoryWriteInput
    OutputModel: ClassVar[type[BaseModel]] = MemoryWriteAsyncOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemoryWriteInput)
        if ctx.deps is None:
            raise JsonRpcError(-32603, "tool deps not wired")

        # Submit-time validation step 1: quota. Sync reject on exhaustion so
        # callers can't bypass quota via async.
        await ctx.deps.quotas.check_write(ctx.tenant_id, len(inp.content))

        # Submit-time validation step 2: bare reference-existence. Full
        # access-check is deferred to drain (where tenant_tx is established).
        # This is the spec's "cheap check" — just confirm the target row
        # exists by UUID or slug-tuple. No access semantics leaked because
        # IT-08 cross-team opacity already gates existence probes elsewhere.
        if inp.references:
            await self._check_references_exist(ctx, inp)

        # Submit-time validation step 3: persist to queue.
        request_id = uuid4()
        now = datetime.now(UTC)
        eta = now + timedelta(seconds=ESTIMATED_CONSISTENCY_SECONDS)
        payload_json = inp.model_dump_json()

        async with tenant_tx(ctx.db_pool, ctx.tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO async_write_queue
                    (request_id, tenant_id, team_id, payload, submitted_at, state,
                     client_id, identity_id)
                VALUES ($1, $2, $3, $4::jsonb, $5, 'queued', $6, $7)
                """,
                request_id,
                ctx.tenant_id,
                inp.team_id,  # may be None; drain will resolve default_team_id
                payload_json,
                now,
                ctx.client_id,
                ctx.identity_id,
            )
            await ctx.deps.audit.audit(
                conn,
                action="memory.write_async",
                result="success",
                tenant_id=ctx.tenant_id,
                identity_id=ctx.identity_id,
                client_id=ctx.client_id,
                target_id=request_id,
                target_kind="async_write_request",
                request_id=ctx.request_id,
                details={
                    "content_length": len(inp.content),
                    "type": inp.type,
                    "team_id": str(inp.team_id) if inp.team_id else None,
                    "references_count": len(inp.references or []),
                },
            )

        # Quota: submission counts. Drain will track real embed_tokens
        # separately at persistence time (per blessed Decision 5).
        await ctx.deps.quotas.increment_write(ctx.tenant_id, 0)

        return MemoryWriteAsyncOutput(
            request_id=request_id,
            queued_at=now,
            estimated_consistency_by=eta,
        )

    async def _check_references_exist(self, ctx: ToolContext, inp: MemoryWriteInput) -> None:
        """Cheap existence probe for every referenced memory.

        Uses system_tx (cross-tenant); existence is bounded by the IT-08
        opacity contract since the only thing leaked is "does this UUID
        correspond to a memory row" — same as what `resolve_reference_target`
        already gates at the application layer.
        """
        assert inp.references is not None
        if not inp.references:
            return

        async with system_tx(ctx.db_pool) as conn:
            for i, ref in enumerate(inp.references):
                if ref.target_uuid is not None:
                    exists = await conn.fetchval(
                        "SELECT 1 FROM memories WHERE id = $1 AND deleted_at IS NULL",
                        ref.target_uuid,
                    )
                else:
                    # Slug-tuple path. Validators in MemoryWriteInput already
                    # enforce all three are present when target_uuid is None.
                    assert ref.target_team_id is not None
                    assert ref.target_resource_type is not None
                    assert ref.target_slug is not None
                    exists = await conn.fetchval(
                        """
                        SELECT 1
                        FROM slugs s
                        JOIN memories m ON m.id = s.memory_id
                        WHERE s.team_id = $1
                          AND s.resource_type = $2
                          AND s.slug = $3
                          AND m.deleted_at IS NULL
                        """,
                        ref.target_team_id,
                        ref.target_resource_type,
                        ref.target_slug,
                    )
                if exists is None:
                    # IT-08 opaque per ratification 70b43c08; mirror the
                    # write.py polish from PR #299 so the Plugin SDK
                    # _invoke_tool wrapper produces a clean
                    # PluginValidationError(code="memory_not_accessible")
                    # instead of falling back to "jsonrpc_-32602".
                    raise JsonRpcError(
                        -32602,
                        "reference target not found",
                        data={
                            "code": "memory_not_accessible",
                            "errors": [
                                {
                                    "path": f"references[{i}]",
                                    "message": "reference target does not exist at submit time",
                                }
                            ],
                        },
                    )
