"""Shared orchestration helpers for the mindmap_* tools.

Map nodes are ordinary core memories. Rather than re-implement the 600-line
embed / dedup / insert / audit path, the mindmap tools create every node by
delegating to MemoryWriteTool, then layer the map graph on top. This keeps
node creation byte-for-byte consistent with memory_write.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import ToolContext
from mem_mcp.mcp.tools.write import MemoryWriteInput, MemoryWriteOutput, MemoryWriteTool


async def resolve_team_id(conn: Any, ctx: ToolContext, explicit: UUID | None) -> UUID:
    """Resolve the effective team_id: explicit, else the caller's default."""
    if explicit is not None:
        return explicit
    team_id = await conn.fetchval(
        """
        SELECT default_team_id FROM tenant_identities
        WHERE tenant_id = $1 AND is_primary = true LIMIT 1
        """,
        ctx.tenant_id,
    )
    if team_id is None:
        raise JsonRpcError(
            -32602,
            "no team_id and caller has no default_team_id; pass team_id explicitly",
            data={"errors": [{"path": "team_id", "message": "team_id required"}]},
        )
    return team_id


async def write_node_memory(
    ctx: ToolContext,
    *,
    content: str,
    mem_type: str,
    team_id: UUID,
    metadata: dict[str, Any],
    slug_clue: str | None = None,
    references: list[dict[str, Any]] | None = None,
    force_new: bool = True,
) -> MemoryWriteOutput:
    """Create a memory through the canonical write path; return its output."""
    inp = MemoryWriteInput(
        content=content,
        type=mem_type,  # type: ignore[arg-type]
        team_id=team_id,
        metadata=metadata,
        slug_clue=slug_clue,
        force_new=force_new,
        references=references,  # type: ignore[arg-type]
        visibility="team",
    )
    out = await MemoryWriteTool()(ctx, inp)
    assert isinstance(out, MemoryWriteOutput)
    return out
