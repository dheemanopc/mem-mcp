"""Tool ABC + per-request ToolContext.

Each MCP tool implements ``BaseTool``. The transport dispatcher passes a
``ToolContext`` carrying the authenticated tenant, scopes, DB pool, etc.

Tools are registered in ``ToolRegistry`` (mem_mcp.mcp.registry). Each tool
declares its required scope; the registry checks scope before dispatch and
raises JsonRpcError(-32000, code='insufficient_scope') if not granted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Protocol
from uuid import UUID

from pydantic import BaseModel

if TYPE_CHECKING:
    import asyncpg

    from mem_mcp.audit.logger import AuditLogger  # future T-5.12; placeholder type


@dataclass(frozen=True)
class ToolContext:
    """Per-request context passed to every tool.

    Built by the transport from the Bearer middleware's TenantContext +
    process-level singletons (db_pool, audit logger).
    """

    request_id: str
    tenant_id: UUID
    identity_id: UUID
    client_id: str
    scopes: frozenset[str]
    db_pool: "asyncpg.Pool"
    # audit: "AuditLogger"  # T-5.12 — left out for now; tools just _log via structlog


class BaseTool(Protocol):
    """Every MCP tool implements this Protocol.

    Concrete tools subclass-style or define class attributes matching it.
    """

    name: ClassVar[str]
    required_scope: ClassVar[str]
    InputModel: ClassVar[type[BaseModel]]
    OutputModel: ClassVar[type[BaseModel]]

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel: ...
