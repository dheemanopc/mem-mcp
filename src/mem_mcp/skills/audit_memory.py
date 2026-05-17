"""Audit-memory writer for skill tool calls.

After every audited skill tool invocation, the loader writes a type=fact memory
tagged ``ai-trader-event`` so the AI Trader v2 cockpit can query skill activity
(or other downstream consumers can subscribe to it).

Memories written here SKIP embedding — they use a fixed constant 1024-d vector.
Retrieval is intended to be tag/metadata-driven, not semantic.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]

from mem_mcp.skills.base import SkillCallContext

# Constant audit-memory embedding — every audit row gets this identical vector.
# Saves a Bedrock call per skill tool invocation. Semantic search across audit
# memories is meaningless by design; tag/metadata filter is the access pattern.
_AUDIT_EMBEDDING: list[float] = [0.001] * 1024


def hash_args(args: dict[str, Any]) -> str:
    """Stable sha256 of args. Used as args_hash in audit metadata."""
    canonical = json.dumps(args, separators=(",", ":"), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _tool_tag_segment(tool_name: str) -> str:
    """Convert snake_case tool_name to a kebab-ish tag segment."""
    return tool_name.replace("_", "-")


def build_audit_tags(
    skill_name: str,
    tool_name: str,
    result_status: str,
    extra_tags: list[str] | None = None,
) -> list[str]:
    """Compute the tag list for a skill audit memory."""
    tags: list[str] = [
        "ai-trader-event",
        f"skill-{skill_name}",
        f"skill-tool-{_tool_tag_segment(tool_name)}",
        f"skill-result-{result_status}",
    ]
    if extra_tags:
        for t in extra_tags:
            if t not in tags:
                tags.append(t)
    return tags


def build_audit_content(
    skill_name: str,
    tool_name: str,
    result_status: str,
    ctx: SkillCallContext,
    elapsed_ms: int,
) -> str:
    """Short markdown summary for the memory's content field."""
    return (
        f"# {skill_name}.{tool_name} — {result_status}\n"
        f"\n"
        f"tenant: {ctx.tenant_id}\n"
        f"request_id: {ctx.request_id}\n"
        f"tool_call_id: {ctx.tool_call_id}\n"
        f"elapsed_ms: {elapsed_ms}\n"
    )


def build_audit_metadata(
    skill_name: str,
    tool_name: str,
    args: dict[str, Any],
    result_status: str,
    elapsed_ms: int,
    tool_call_id: UUID,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """Assemble the metadata payload for a skill audit memory."""
    meta: dict[str, Any] = {
        "tool_call_id": str(tool_call_id),
        "args_hash": hash_args(args),
        "result_status": result_status,
        "elapsed_ms": int(elapsed_ms),
        "skill_name": skill_name,
        "tool_name": tool_name,
    }
    if error_code is not None:
        meta["error_code"] = error_code
    if error_message is not None:
        meta["error_message"] = error_message[:500]
    return meta


def should_audit(audit_policy: dict[str, bool] | None, tool_name: str) -> bool:
    """Per-skill audit policy: dict maps tool_name -> bool. Default True if missing."""
    if audit_policy is None:
        return True
    return audit_policy.get(tool_name, True)


async def write_skill_audit_memory(
    conn: asyncpg.Connection,
    ctx: SkillCallContext,
    skill_name: str,
    tool_name: str,
    args: dict[str, Any],
    result_status: str,
    elapsed_ms: int,
    error_code: str | None = None,
    error_message: str | None = None,
    extra_tags: list[str] | None = None,
) -> UUID | None:
    """Write a type=fact audit memory for a skill tool invocation.

    Returns the new memory id, or None if write was skipped (this happens
    only when the caller passes a malformed result_status — defensive).

    Caller MUST be inside ``tenant_tx`` so RLS is engaged.
    """
    if result_status not in ("success", "error"):
        return None

    tags = build_audit_tags(skill_name, tool_name, result_status, extra_tags)
    content = build_audit_content(skill_name, tool_name, result_status, ctx, elapsed_ms)
    metadata = build_audit_metadata(
        skill_name=skill_name,
        tool_name=tool_name,
        args=args,
        result_status=result_status,
        elapsed_ms=elapsed_ms,
        tool_call_id=ctx.tool_call_id,
        error_code=error_code,
        error_message=error_message,
    )
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    row = await conn.fetchrow(
        """
        INSERT INTO memories (
            tenant_id, content, content_hash, embedding,
            source_client_id, source_kind,
            type, tags, metadata, version, is_current
        ) VALUES ($1, $2, $3, $4::vector, $5, 'api', 'fact', $6, $7::jsonb, 1, true)
        RETURNING id
        """,
        ctx.tenant_id,
        content,
        content_hash,
        _AUDIT_EMBEDDING,
        ctx.client_id,
        tags,
        json.dumps(metadata),
    )
    return row["id"] if row else None
