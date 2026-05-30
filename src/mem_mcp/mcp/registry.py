"""ToolRegistry — register tools by name, dispatch JSON-RPC methods, scope-check."""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from mem_mcp.logging_setup import get_logger
from mem_mcp.mcp.errors import JsonRpcError

if TYPE_CHECKING:
    from mem_mcp.mcp.tools._base import BaseTool, ToolContext
    from mem_mcp.plugins.integration import RegisteredTool


_log = get_logger("mem_mcp.mcp.registry")


class ToolRegistry:
    """Holds the set of registered tools; dispatches JSON-RPC by method name."""

    def __init__(self) -> None:
        self._tools: dict[str, type[BaseTool]] = {}

    def register(self, tool_cls: type[BaseTool]) -> None:
        """Register a tool class. Idempotent (overwrites by name)."""
        self._tools[tool_cls.name] = tool_cls

    def register_plugin_tool(self, registered_tool: RegisteredTool) -> None:
        """Register a plugin's RegisteredTool by wrapping it as a BaseTool-compatible class.

        The adapter exposes the same interface as BaseTool so the dispatcher
        (dispatch method) can treat it uniformly with native tools.

        Uses type() to dynamically construct the adapter class, avoiding Python's
        class-body closure restrictions (class bodies cannot reference outer function locals).
        """
        rt = registered_tool
        # rt.required_permission is already a string (normalized in ToolRegistryImpl).
        required_scope = rt.required_permission

        async def call_impl(self: Any, ctx: ToolContext, inp: BaseModel) -> BaseModel:
            """Invoke the plugin handler with (inp, tenant_id, request_id)."""
            result = await rt.handler(inp, ctx.tenant_id, ctx.request_id)
            assert isinstance(result, BaseModel)
            return result

        # Build adapter class using type() to capture rt and required_scope
        # in the namespace dict (not via closure).
        adapter = type(
            f"PluginToolAdapter_{rt.namespaced_name.replace('.', '_')}",
            (),
            {
                "name": rt.namespaced_name,
                "description": rt.description,
                "required_scope": required_scope,
                "InputModel": rt.input_schema,
                "OutputModel": rt.input_schema,  # TODO: store output schema in RegisteredTool
                "__call__": call_impl,
            },
        )

        # Register the adapter
        self.register(adapter)
        _log.info(
            "plugin_tool_registered_in_mcp",
            tool_name=rt.namespaced_name,
            plugin_id=rt.plugin_id,
        )

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def list_definitions(self) -> list[dict[str, Any]]:
        """Return a list of {name, required_scope, inputSchema} for tools/list."""
        defs: list[dict[str, Any]] = []
        for name in sorted(self._tools.keys()):
            cls = self._tools[name]
            defs.append(
                {
                    "name": name,
                    "description": cls.description,
                    "inputSchema": cls.InputModel.model_json_schema(),
                    "outputSchema": cls.OutputModel.model_json_schema(),
                    "required_scope": cls.required_scope,
                }
            )
        return defs

    async def dispatch(
        self,
        ctx: ToolContext,
        method: str,
        params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Look up tool, check scope, validate input, call, serialize output.

        Raises JsonRpcError on any failure (caller serializes to JSON-RPC envelope).
        """
        tool_cls = self._tools.get(method)
        if tool_cls is None:
            raise JsonRpcError(
                -32601,
                f"method not found: {method!r}",
                data={"available_methods": self.names()},
            )

        if tool_cls.required_scope and tool_cls.required_scope not in ctx.scopes:
            raise JsonRpcError(
                -32000,
                "insufficient scope",
                data={
                    "code": "insufficient_scope",
                    "required_scopes": [tool_cls.required_scope],
                    "granted_scopes": sorted(ctx.scopes),
                },
            )

        try:
            inp = tool_cls.InputModel.model_validate(params or {})
        except ValidationError as exc:
            raise JsonRpcError(
                -32602,
                "invalid params",
                data={"errors": exc.errors(include_url=False, include_input=False)},
            ) from exc

        instance = tool_cls()  # Protocol — constructable
        try:
            output: BaseModel = await instance(ctx, inp)
        except JsonRpcError:
            raise
        except Exception as exc:
            # Catch-all: log enough to diagnose + surface exc type/message in
            # error.data so partner apps don't have to grep mem-mcp logs.
            # Full traceback stays server-side only.
            _log.error(
                "tool_dispatch_unhandled_exception",
                tool=method,
                exc_type=type(exc).__name__,
                exc_message=str(exc)[:500],
                traceback=traceback.format_exc(),
            )
            raise JsonRpcError(
                -32603,
                "internal error",
                data={
                    "exc_type": type(exc).__name__,
                    "message": str(exc)[:300],
                },
            ) from exc

        return output.model_dump(mode="json")
