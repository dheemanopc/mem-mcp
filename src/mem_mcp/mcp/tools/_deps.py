"""Tool dependencies — the cross-cutting collaborators every tool needs.

A ToolDeps instance is built once at startup (from settings + DB pool) and
attached to each ToolContext. Per GUIDELINES §1.2, all members are
Protocol-shaped so tests can swap in fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from mem_mcp.audit.logger import AuditLogger, NoopAuditLogger

if TYPE_CHECKING:
    from mem_mcp.embeddings.bedrock import EmbeddingClient


class Quotas(Protocol):
    """Quota enforcement boundary. Real impl lands in T-7.9."""

    async def check_write(self, tenant_id: UUID, content_len_estimate: int) -> None: ...
    async def check_read(self, tenant_id: UUID) -> None: ...
    async def increment_write(self, tenant_id: UUID, embed_tokens: int) -> None: ...
    async def increment_read(self, tenant_id: UUID, embed_tokens: int) -> None: ...


class NoopQuotas:
    """Allows everything. Used in v1 boot until T-7.9 ships the real enforcer."""

    async def check_write(self, tenant_id: UUID, content_len_estimate: int) -> None:
        return None

    async def check_read(self, tenant_id: UUID) -> None:
        return None

    async def increment_write(self, tenant_id: UUID, embed_tokens: int) -> None:
        return None

    async def increment_read(self, tenant_id: UUID, embed_tokens: int) -> None:
        return None


@dataclass(frozen=True)
class ToolDeps:
    """Cross-cutting deps injected into every tool via ToolContext."""

    embeddings: EmbeddingClient
    audit: AuditLogger
    quotas: Quotas


def make_default_deps(
    *,
    embeddings: EmbeddingClient,
    audit: AuditLogger | None = None,
    quotas: Quotas | None = None,
) -> ToolDeps:
    """Build ToolDeps with sensible production defaults for the gaps."""
    return ToolDeps(
        embeddings=embeddings,
        audit=audit or NoopAuditLogger(),
        quotas=quotas or NoopQuotas(),
    )
