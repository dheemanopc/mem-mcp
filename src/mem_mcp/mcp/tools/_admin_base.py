"""AdminTool — base class for the ``memsys_admin_*`` tool family.

Why a base class rather than a check in each tool: an admin tool that forgets
its permission check is a privilege-escalation bug, not a style problem. Here
the gate lives in ``__call__`` and subclasses implement ``run()``, which is
only ever reached after the gate passes. A subclass physically cannot ship
without the check unless someone overrides ``__call__``, and
``test_admin_permission_gate.py`` asserts that no registered admin tool does.

Two layers of authorization, both required:

  1. Coarse OAuth scope (``required_scope``) — enforced by ToolRegistry before
     dispatch, so a token without it never reaches the DB.
  2. Fine RBAC permission (``required_permission``) — enforced below against
     ``tenant_system_roles`` via ``has_permission``.

Layer 2 is the real control. Layer 2 is what stops a non-admin caller: the
read tools currently declare ``memory.read`` as their coarse scope because
``memory.admin`` does not yet exist in the Cognito resource server. Adding it
is a CloudFormation change (GUIDELINES §1.4) which does NOT ship on merge to
main, so gating on it today would land a dead tool. Tighten
``required_scope`` here in the same PR that deploys the resource-server
change, before any *mutating* admin tool lands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel

from mem_mcp.auth.permissions import Permission
from mem_mcp.db import system_tx
from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tools._base import BaseTool, ToolContext

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

    from mem_mcp.audit.logger import AuditAction


class _PermissionDeniedError(Exception):
    """Internal signal. Converted to JsonRpcError once the transaction has closed."""


class AdminTool(BaseTool):
    """Base for admin tools. Subclasses implement ``run``, never ``__call__``."""

    name: ClassVar[str]
    description: ClassVar[str]
    # See module docstring — tightens to "memory.admin" once the Cognito
    # resource server carries it.
    required_scope: ClassVar[str] = "memory.read"
    required_permission: ClassVar[Permission]
    audit_action: ClassVar[AuditAction]
    InputModel: ClassVar[type[BaseModel]]
    OutputModel: ClassVar[type[BaseModel]]

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        # system_tx: admin reads are cross-tenant, so there is no
        # app.current_tenant_id to set. Anything reached from here must filter
        # in SQL rather than leaning on RLS.
        try:
            async with system_tx(ctx.db_pool) as conn:
                if not await self._check_permission(conn, ctx):
                    raise _PermissionDeniedError
                output = await self.run(conn, ctx, inp)
                # Success rides the same transaction on purpose (LLD §4.9):
                # if the operation rolls back, its audit row goes with it.
                await self._audit(conn, ctx, result="success")
                return output
        except _PermissionDeniedError:
            # Failure audits MUST go on a fresh transaction. system_tx wraps
            # the block in conn.transaction(), so a row written just before
            # raising would roll back with the exception and the denial would
            # vanish — the opposite of what an audit trail is for.
            await self._audit_standalone(ctx, result="denied")
            raise JsonRpcError(
                -32603,
                f"caller lacks required permission: {self.required_permission.value}",
                data={"missing_permission": self.required_permission.value},
            ) from None
        except JsonRpcError:
            # run() already chose the client-facing error; audit out of band
            # for the same rollback reason.
            await self._audit_standalone(ctx, result="error")
            raise

    async def _check_permission(self, conn: asyncpg.Connection, ctx: ToolContext) -> bool:
        # Imported lazily: mem_mcp.auth.rbac pulls in FastAPI + web.sessions for
        # its require_permission dependency, which the MCP path does not need.
        # Same pattern as ToolRegistry.register_plugin_tool.
        from mem_mcp.auth.rbac import has_permission

        return await has_permission(conn, ctx.tenant_id, self.required_permission)

    async def _audit_standalone(self, ctx: ToolContext, *, result: str) -> None:
        """Audit on its own transaction, so it survives the caller's rollback."""
        if ctx.deps is None:
            return
        async with system_tx(ctx.db_pool) as conn:
            await self._audit(conn, ctx, result=result)

    async def _audit(
        self,
        conn: asyncpg.Connection,
        ctx: ToolContext,
        *,
        result: str,
    ) -> None:
        """Record the call, allowed or denied.

        Denials are audited precisely because they are the interesting case:
        this tool family is reachable by an LLM, so "who tried what" matters
        as much as "who did what". Never raises — AuditLogger swallows its own
        failures by contract.
        """
        if ctx.deps is None:
            return
        details: dict[str, Any] = {
            "tool": self.name,
            "via": "mcp",
            "required_permission": self.required_permission.value,
        }
        await ctx.deps.audit.audit(
            conn,
            action=self.audit_action,
            result=result,  # type: ignore[arg-type]
            # reason: AuditResult is a Literal; result is constrained by callers above.
            tenant_id=ctx.tenant_id,
            identity_id=ctx.identity_id,
            client_id=ctx.client_id,
            request_id=ctx.request_id,
            details=details,
        )

    async def run(
        self,
        conn: asyncpg.Connection,
        ctx: ToolContext,
        inp: BaseModel,
    ) -> BaseModel:
        """Do the work. Called only after the permission gate passes."""
        raise NotImplementedError
