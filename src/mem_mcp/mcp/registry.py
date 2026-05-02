"""ToolRegistry — register tools by name, dispatch JSON-RPC methods, scope-check."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from mem_mcp.mcp.errors import JsonRpcError

if TYPE_CHECKING:
    from mem_mcp.mcp.tools._base import BaseTool, ToolContext


class ToolRegistry:
    """Holds the set of registered tools; dispatches JSON-RPC by method name."""

    def __init__(self) -> None:
        self._tools: dict[str, type[BaseTool]] = {}

    def register(self, tool_cls: type[BaseTool]) -> None:
        """Register a tool class. Idempotent (overwrites by name)."""
        self._tools[tool_cls.name] = tool_cls

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
                    "description": (cls.__doc__ or "").strip().splitlines()[0]
                    if cls.__doc__
                    else "",
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
            # Don't leak internals; log with full stack via structlog (caller does).
            raise JsonRpcError(
                -32603,
                "internal error",
                data={"detail": type(exc).__name__},
            ) from exc

        return output.model_dump(mode="json")
