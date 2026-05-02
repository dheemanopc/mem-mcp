"""MCP (Model Context Protocol) transport + tool dispatcher for mem-mcp.

Per MCP spec 2025-06-18: Streamable HTTP transport on POST /mcp using
JSON-RPC 2.0. Per spec §9, mem-mcp exposes 11 tools (memory.write/search/
get/list/update/delete/undelete/supersede/export/stats/feedback) — those
are added in T-5.5 onwards.

Public API:
    BaseTool         — Protocol every tool implements
    ToolContext      — per-request context (tenant + scopes + db_pool + audit)
    ToolRegistry     — register tools; dispatch a JSON-RPC method by name
    make_mcp_router  — FastAPI router for POST /mcp
    JsonRpcError     — typed exception with .code (-32xxx) and .data
"""

from mem_mcp.mcp.errors import (
    JsonRpcError,
    JsonRpcErrorCode,
    to_jsonrpc_error_response,
)
from mem_mcp.mcp.registry import ToolRegistry
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.mcp.transport import make_mcp_router

__all__ = [
    "BaseTool",
    "JsonRpcError",
    "JsonRpcErrorCode",
    "ToolContext",
    "ToolRegistry",
    "make_mcp_router",
    "to_jsonrpc_error_response",
]
