"""POST /mcp Streamable HTTP transport for JSON-RPC 2.0.

Per spec §9.1:
  - Single endpoint POST /mcp
  - JSON-RPC 2.0 envelopes
  - SSE (text/event-stream) on demand for multi-message responses
  - Bearer JWT required (handled by mem_mcp.auth.middleware.bearer_middleware)
  - Origin validated against allowlist when present
  - Optional Mcp-Session-Id header tolerated; v1 stateless

For v1 we serve plain application/json (no SSE) — multi-message is a tools
problem (none of memory.* tools stream). SSE wiring is left as a TODO.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from mem_mcp.logging_setup import get_logger
from mem_mcp.mcp.errors import JsonRpcError, to_jsonrpc_error_response
from mem_mcp.mcp.tools._base import ToolContext

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

    from mem_mcp.mcp.registry import ToolRegistry
    from mem_mcp.mcp.tools._deps import ToolDeps


_log = get_logger("mem_mcp.mcp.transport")


def make_mcp_router(
    *,
    registry: ToolRegistry,
    db_pool: asyncpg.Pool,
    deps: "ToolDeps",
) -> APIRouter:
    """Build the POST /mcp router. The Bearer middleware must run before this."""
    router = APIRouter(tags=["mcp"])

    @router.post("/mcp")
    async def mcp_handler(request: Request) -> JSONResponse:
        # Allocate a request_id for correlation (also used in JSON-RPC error envelopes
        # when the request can't be parsed)
        req_id = str(uuid.uuid4())
        request_id: str | int | None = None

        # Parse JSON-RPC envelope
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(
                content=to_jsonrpc_error_response(None, -32700, "parse error"),
                status_code=400,
            )

        # Single request only in v1 (no batches per MCP 2025-06-18)
        if not isinstance(body, dict):
            return JSONResponse(
                content=to_jsonrpc_error_response(None, -32600, "invalid request: must be object"),
                status_code=400,
            )

        request_id = body.get("id")  # may be str, int, or null

        if body.get("jsonrpc") != "2.0":
            return JSONResponse(
                content=to_jsonrpc_error_response(request_id, -32600, "jsonrpc must be '2.0'"),
                status_code=400,
            )

        method = body.get("method")
        if not isinstance(method, str):
            return JSONResponse(
                content=to_jsonrpc_error_response(request_id, -32600, "method must be string"),
                status_code=400,
            )

        # tools/list — registry built-in (not a tool itself)
        if method == "tools/list":
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": registry.list_definitions()},
                }
            )

        # MCP protocol handshake — must be handled before tenant context check
        if method == "initialize":
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "mem-mcp", "version": "1.0.0"},
                    },
                }
            )

        # MCP notifications are fire-and-forget; no response body expected
        if method.startswith("notifications/"):
            return JSONResponse(content=None, status_code=202)

        # Build ToolContext from Bearer middleware's request.state.tenant_ctx
        tenant_ctx = getattr(request.state, "tenant_ctx", None)
        if tenant_ctx is None:
            # Should never happen if middleware is wired correctly.
            return JSONResponse(
                content=to_jsonrpc_error_response(
                    request_id, -32603, "tenant context missing — middleware not wired"
                ),
                status_code=500,
            )

        ctx = ToolContext(
            request_id=req_id,
            tenant_id=tenant_ctx.tenant_id,
            identity_id=tenant_ctx.identity_id,
            client_id=tenant_ctx.client_id,
            scopes=tenant_ctx.scopes,
            db_pool=db_pool,
            deps=deps,
        )

        params = body.get("params") or {}
        if not isinstance(params, dict):
            return JSONResponse(
                content=to_jsonrpc_error_response(
                    request_id, -32602, "params must be object or omitted"
                ),
                status_code=400,
            )

        if method == "tools/call":
            tool_name = params.get("name")
            if not isinstance(tool_name, str) or not tool_name:
                return JSONResponse(
                    content=to_jsonrpc_error_response(
                        request_id, -32602, "tools/call requires params.name"
                    ),
                    status_code=400,
                )
            tool_args = params.get("arguments") or {}
            if not isinstance(tool_args, dict):
                return JSONResponse(
                    content=to_jsonrpc_error_response(
                        request_id, -32602, "tools/call params.arguments must be object"
                    ),
                    status_code=400,
                )
            dispatch_method = tool_name
            dispatch_params = tool_args
        else:
            dispatch_method = method
            dispatch_params = params

        try:
            result = await registry.dispatch(ctx, dispatch_method, dispatch_params)
        except JsonRpcError as err:
            _log.info(
                "mcp_dispatch_error",
                request_id=req_id,
                method=method,
                tenant_id=str(ctx.tenant_id),
                code=err.code,
            )
            return JSONResponse(
                content=err.to_envelope(request_id),
                status_code=200,  # JSON-RPC errors travel in 200 envelopes
            )

        if method == "tools/call":
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(result)}],
                        "isError": False,
                    },
                }
            )
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        )

    return router
