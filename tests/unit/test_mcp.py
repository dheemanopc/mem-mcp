"""Tests for mem_mcp.mcp (T-5.1 + T-5.2 + T-5.3)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

from mem_mcp.auth.middleware import TenantContext
from mem_mcp.auth.permissions import Permission
from mem_mcp.mcp.errors import JsonRpcError, to_jsonrpc_error_response
from mem_mcp.mcp.registry import ToolRegistry
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.mcp.tools._test_echo import EchoTool
from mem_mcp.mcp.transport import make_mcp_router
from mem_mcp.plugins.integration import RegisteredTool

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _ctx(scopes: tuple[str, ...] = ("memory.read", "memory.write")) -> ToolContext:
    return ToolContext(
        request_id=str(uuid4()),
        tenant_id=uuid4(),
        identity_id=uuid4(),
        client_id="client-1",
        scopes=frozenset(scopes),
        db_pool=MagicMock(),  # not touched by EchoTool
    )


def _build_app(registry: ToolRegistry, scopes: tuple[str, ...] = ("memory.read",)) -> TestClient:
    """Build a FastAPI app with /mcp wired + a fake middleware that injects tenant_ctx."""
    from mem_mcp.audit.logger import NoopAuditLogger
    from mem_mcp.mcp.tools._deps import NoopQuotas, ToolDeps

    app = FastAPI()
    fake_db = MagicMock()
    deps = ToolDeps(
        embeddings=MagicMock(),
        audit=NoopAuditLogger(),
        quotas=NoopQuotas(),
    )
    app.include_router(make_mcp_router(registry=registry, db_pool=fake_db, deps=deps))

    @app.middleware("http")
    async def _inject_ctx(request, call_next):  # type: ignore[no-untyped-def]
        request.state.tenant_ctx = TenantContext(
            tenant_id=uuid4(),
            identity_id=uuid4(),
            client_id="client-1",
            scopes=frozenset(scopes),
        )
        return await call_next(request)

    return TestClient(app)


def _rpc(method: str, params: dict[str, Any] | None = None, *, id: Any = 1) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        body["params"] = params
    return body


# --------------------------------------------------------------------------
# errors.py
# --------------------------------------------------------------------------


class TestJsonRpcError:
    def test_basic_envelope(self) -> None:
        env = JsonRpcError(-32601, "not found").to_envelope(7)
        assert env == {"jsonrpc": "2.0", "id": 7, "error": {"code": -32601, "message": "not found"}}

    def test_with_data(self) -> None:
        env = JsonRpcError(-32000, "bad", data={"code": "quota_exceeded"}).to_envelope("abc")
        assert env["error"]["data"] == {"code": "quota_exceeded"}
        assert env["id"] == "abc"

    def test_helper_function(self) -> None:
        env = to_jsonrpc_error_response(None, -32700, "parse error")
        assert env["id"] is None
        assert env["error"]["code"] == -32700


# --------------------------------------------------------------------------
# ToolRegistry
# --------------------------------------------------------------------------


class TestToolRegistry:
    def test_register_and_names(self) -> None:
        r = ToolRegistry()
        r.register(EchoTool)
        assert r.names() == ["_test.echo"]

    def test_register_idempotent(self) -> None:
        r = ToolRegistry()
        r.register(EchoTool)
        r.register(EchoTool)
        assert r.names() == ["_test.echo"]

    def test_list_definitions_includes_schema(self) -> None:
        r = ToolRegistry()
        r.register(EchoTool)
        defs = r.list_definitions()
        assert len(defs) == 1
        d = defs[0]
        assert d["name"] == "_test.echo"
        assert d["required_scope"] == "memory.read"
        assert "inputSchema" in d
        assert d["inputSchema"]["type"] == "object"
        assert "message" in d["inputSchema"]["properties"]

    @pytest.mark.asyncio
    async def test_dispatch_unknown_method(self) -> None:
        r = ToolRegistry()
        ctx = _ctx()
        with pytest.raises(JsonRpcError) as exc_info:
            await r.dispatch(ctx, "no.such.method", {})
        assert exc_info.value.code == -32601

    @pytest.mark.asyncio
    async def test_dispatch_insufficient_scope(self) -> None:
        r = ToolRegistry()
        r.register(EchoTool)
        ctx = _ctx(scopes=())  # no scopes
        with pytest.raises(JsonRpcError) as exc_info:
            await r.dispatch(ctx, "_test.echo", {"message": "hi"})
        assert exc_info.value.code == -32000
        assert exc_info.value.data is not None
        assert exc_info.value.data["code"] == "insufficient_scope"

    @pytest.mark.asyncio
    async def test_dispatch_invalid_params(self) -> None:
        r = ToolRegistry()
        r.register(EchoTool)
        ctx = _ctx()
        with pytest.raises(JsonRpcError) as exc_info:
            await r.dispatch(ctx, "_test.echo", {})  # missing 'message'
        assert exc_info.value.code == -32602
        assert exc_info.value.data is not None
        assert "errors" in exc_info.value.data

    @pytest.mark.asyncio
    async def test_dispatch_success(self) -> None:
        r = ToolRegistry()
        r.register(EchoTool)
        ctx = _ctx()
        result = await r.dispatch(ctx, "_test.echo", {"message": "hello"})
        assert result["echoed"] == "hello"
        assert result["request_id"] == ctx.request_id

    @pytest.mark.asyncio
    async def test_dispatch_internal_exception_to_32603(self) -> None:
        """If a tool raises a non-JsonRpcError, dispatch wraps it as -32603."""

        class _CrashInput(BaseModel):
            model_config = ConfigDict(extra="forbid")
            x: str = Field(default="ok")

        class _CrashOutput(BaseModel):
            ok: bool

        class _CrashTool(BaseTool):
            name = "_test.crash"
            required_scope = "memory.read"
            InputModel = _CrashInput
            OutputModel = _CrashOutput

            async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
                raise RuntimeError("oops")

        r = ToolRegistry()
        r.register(_CrashTool)  # type: ignore[type-abstract]
        with pytest.raises(JsonRpcError) as exc_info:
            await r.dispatch(_ctx(), "_test.crash", {})
        assert exc_info.value.code == -32603
        # Observability: error.data now carries the exception type + message
        # so partner apps don't have to grep mem-mcp logs to debug failures.
        assert exc_info.value.data is not None
        assert exc_info.value.data["exc_type"] == "RuntimeError"
        assert "oops" in exc_info.value.data["message"]

    def test_register_plugin_tool_no_required_permission(self) -> None:
        """Plugin tool without required_permission registers successfully."""

        class _PluginInput(BaseModel):
            message: str

        class _PluginOutput(BaseModel):
            result: str

        async def plugin_handler(
            inp: _PluginInput, tenant_id: Any, request_id: str
        ) -> _PluginOutput:
            return _PluginOutput(result=f"handled: {inp.message}")

        registered = RegisteredTool(
            plugin_id="test-plugin",
            namespaced_name="test-plugin.echo",
            description="Test echo tool",
            input_schema=_PluginInput,
            handler=plugin_handler,
            required_permission=None,
        )

        r = ToolRegistry()
        r.register_plugin_tool(registered)

        # Verify tool was registered
        assert "test-plugin.echo" in r.names()
        defs = r.list_definitions()
        assert len(defs) == 1
        assert defs[0]["name"] == "test-plugin.echo"
        assert defs[0]["required_scope"] is None

    def test_register_plugin_tool_with_required_permission(self) -> None:
        """Plugin tool with required_permission registers with scope."""

        class _PluginInput(BaseModel):
            value: str

        class _PluginOutput(BaseModel):
            ok: bool

        async def plugin_handler(
            inp: _PluginInput, tenant_id: Any, request_id: str
        ) -> _PluginOutput:
            return _PluginOutput(ok=True)

        registered = RegisteredTool(
            plugin_id="test-plugin",
            namespaced_name="test-plugin.secure",
            description="Test secure tool",
            input_schema=_PluginInput,
            handler=plugin_handler,
            required_permission=Permission.TEAM_MANAGE_MEMBERS,
        )

        r = ToolRegistry()
        r.register_plugin_tool(registered)

        # Verify tool was registered with correct scope
        assert "test-plugin.secure" in r.names()
        defs = r.list_definitions()
        assert len(defs) == 1
        assert defs[0]["required_scope"] == "team.manage_members"

    @pytest.mark.asyncio
    async def test_register_plugin_tool_dispatch(self) -> None:
        """Plugin tool adapter can be dispatched successfully."""

        class _PluginInput(BaseModel):
            message: str

        class _PluginOutput(BaseModel):
            echoed: str

        call_count = 0

        async def plugin_handler(
            inp: _PluginInput, tenant_id: Any, request_id: str
        ) -> _PluginOutput:
            nonlocal call_count
            call_count += 1
            return _PluginOutput(echoed=inp.message)

        registered = RegisteredTool(
            plugin_id="test-plugin",
            namespaced_name="test-plugin.call",
            description="Test callable tool",
            input_schema=_PluginInput,
            handler=plugin_handler,
            required_permission=None,
        )

        r = ToolRegistry()
        r.register_plugin_tool(registered)

        # Dispatch and verify handler was called
        ctx = _ctx()
        result = await r.dispatch(ctx, "test-plugin.call", {"message": "hello"})
        assert result["echoed"] == "hello"
        assert call_count == 1


# --------------------------------------------------------------------------
# transport — POST /mcp
# --------------------------------------------------------------------------


class TestTransport:
    def test_tools_list(self) -> None:
        r = ToolRegistry()
        r.register(EchoTool)
        client = _build_app(r)
        resp = client.post("/mcp", json=_rpc("tools/list"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["jsonrpc"] == "2.0"
        assert body["id"] == 1
        assert "result" in body
        assert body["result"]["tools"][0]["name"] == "_test.echo"

    def test_invalid_json_body(self) -> None:
        r = ToolRegistry()
        client = _build_app(r)
        resp = client.post(
            "/mcp", content=b"not json", headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == -32700

    def test_non_object_body(self) -> None:
        r = ToolRegistry()
        client = _build_app(r)
        resp = client.post("/mcp", json=[])
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == -32600

    def test_missing_jsonrpc_version(self) -> None:
        r = ToolRegistry()
        client = _build_app(r)
        resp = client.post("/mcp", json={"id": 1, "method": "x"})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == -32600

    def test_method_not_string(self) -> None:
        r = ToolRegistry()
        client = _build_app(r)
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": 123})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == -32600

    def test_unknown_method_returns_envelope_in_200(self) -> None:
        """JSON-RPC errors live inside the envelope with HTTP 200 (per spec convention)."""
        r = ToolRegistry()
        client = _build_app(r)
        resp = client.post("/mcp", json=_rpc("no.such.method"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"]["code"] == -32601

    def test_insufficient_scope_envelope(self) -> None:
        r = ToolRegistry()
        r.register(EchoTool)
        client = _build_app(r, scopes=())  # no scopes granted
        resp = client.post("/mcp", json=_rpc("_test.echo", {"message": "hi"}))
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"]["code"] == -32000
        assert body["error"]["data"]["code"] == "insufficient_scope"

    def test_validation_error_envelope(self) -> None:
        r = ToolRegistry()
        r.register(EchoTool)
        client = _build_app(r)
        resp = client.post("/mcp", json=_rpc("_test.echo", {}))  # missing message
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"]["code"] == -32602

    def test_successful_tool_call(self) -> None:
        r = ToolRegistry()
        r.register(EchoTool)
        client = _build_app(r)
        resp = client.post("/mcp", json=_rpc("_test.echo", {"message": "hello"}))
        assert resp.status_code == 200
        body = resp.json()
        assert body["jsonrpc"] == "2.0"
        assert body["id"] == 1
        assert body["result"]["echoed"] == "hello"
        assert "request_id" in body["result"]

    def test_string_id_preserved(self) -> None:
        r = ToolRegistry()
        r.register(EchoTool)
        client = _build_app(r)
        resp = client.post("/mcp", json=_rpc("_test.echo", {"message": "x"}, id="req-abc"))
        assert resp.json()["id"] == "req-abc"

    def test_null_id_preserved(self) -> None:
        r = ToolRegistry()
        r.register(EchoTool)
        client = _build_app(r)
        resp = client.post("/mcp", json=_rpc("_test.echo", {"message": "x"}, id=None))
        assert resp.json()["id"] is None
