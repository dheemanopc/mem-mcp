"""T8 Tier-1 SDK extension — unit tests.

Per Test Plan v1 `0a24fda8` + v2 delta `688951fa`. Covers:
- Signature shape of all extended/new methods (U1, U6, U8, U12, U16, U20-U22, U23).
- Kwarg forwarding to underlying tools (U3-U5, U7, U9-U11, U13-U15, U18).
- Type-restriction enforcement on update (U17, U18, U19).
- _invoke_tool wrapper behavior (U24-U26).
- get() back-compat carve-out (U27).
- PluginValidationError shape.
"""

from __future__ import annotations

import inspect
from typing import Any, ClassVar
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.plugins.clients.memory import MemoryClientImpl
from mem_mcp.plugins.contract import MemoryClient, PluginValidationError

# ── Fixtures ───────────────────────────────────────────────────────────────


def _make_impl() -> MemoryClientImpl:
    """Build a MemoryClientImpl with all dependencies mocked.

    Returned impl can be called against; underlying tools must be patched
    with monkeypatch in each test that exercises a real tool invocation.
    """
    pool = MagicMock()
    deps = MagicMock()
    return MemoryClientImpl(
        pool=pool,
        tenant_id=uuid4(),
        plugin_id="test-plugin",
        deps=deps,
        identity_id=uuid4(),
    )


# ── PluginValidationError shape ────────────────────────────────────────────


class TestPluginValidationError:
    """U23 — exception shape contract."""

    def test_shape(self) -> None:
        exc = PluginValidationError(code="x", message="y", data={"k": 1})
        assert exc.code == "x"
        assert exc.message == "y"
        assert exc.data == {"k": 1}
        assert str(exc) == "x: y"

    def test_default_data_is_empty_dict(self) -> None:
        exc = PluginValidationError(code="x", message="y")
        assert exc.data == {}


# ── _invoke_tool wrapper behavior ─────────────────────────────────────────


class TestInvokeToolWrapper:
    """U24, U25, U26 — wrapper converts substrate errors to PluginValidationError."""

    async def test_wraps_pydantic_validation_error(self) -> None:
        impl = _make_impl()

        class _InputRejects(BaseModel):
            model_config = ConfigDict(extra="forbid")
            n: int

        # Tool that triggers ValidationError on call via invalid kwargs
        async def _raising(_ctx: Any, _inp: Any) -> Any:
            _InputRejects(n="not-an-int")  # type: ignore[arg-type]
            return None

        with pytest.raises(PluginValidationError) as ei:
            await impl._invoke_tool(_raising, MagicMock(), MagicMock())
        assert ei.value.code == "invalid_params"
        assert "errors" in ei.value.data

    async def test_wraps_jsonrpc_with_structured_code(self) -> None:
        impl = _make_impl()

        async def _raising(_ctx: Any, _inp: Any) -> Any:
            raise JsonRpcError(
                -32602,
                "bad reference",
                data={"code": "memory_not_accessible", "errors": []},
            )

        with pytest.raises(PluginValidationError) as ei:
            await impl._invoke_tool(_raising, MagicMock(), MagicMock())
        assert ei.value.code == "memory_not_accessible"
        assert ei.value.message == "bad reference"

    async def test_wraps_jsonrpc_without_data_code_uses_fallback(self) -> None:
        impl = _make_impl()

        async def _raising(_ctx: Any, _inp: Any) -> Any:
            raise JsonRpcError(-32603, "something", data={"errors": []})

        with pytest.raises(PluginValidationError) as ei:
            await impl._invoke_tool(_raising, MagicMock(), MagicMock())
        assert ei.value.code == "jsonrpc_-32603"

    async def test_wraps_jsonrpc_with_data_none(self) -> None:
        impl = _make_impl()

        async def _raising(_ctx: Any, _inp: Any) -> Any:
            raise JsonRpcError(-32603, "boom", data=None)

        with pytest.raises(PluginValidationError) as ei:
            await impl._invoke_tool(_raising, MagicMock(), MagicMock())
        assert ei.value.code == "jsonrpc_-32603"
        assert ei.value.data == {}

    async def test_preserves_success_path(self) -> None:
        impl = _make_impl()
        sentinel = MagicMock()

        async def _ok(_ctx: Any, _inp: Any) -> Any:
            return sentinel

        result = await impl._invoke_tool(_ok, MagicMock(), MagicMock())
        assert result is sentinel


# ── Protocol-level signature checks ─────────────────────────────────────────


class TestSignatures:
    """U1, U6, U8, U12, U16, U20-U22, U23 — surface shape checks."""

    def test_write_signature_extended(self) -> None:
        params = inspect.signature(MemoryClient.write).parameters
        assert "parent_id" in params
        assert "indexable" in params
        assert "references" in params
        # All three must be keyword-only
        for name in ("parent_id", "indexable", "references"):
            assert params[name].kind == inspect.Parameter.KEYWORD_ONLY

    def test_thread_get_signature(self) -> None:
        sig = inspect.signature(MemoryClient.thread_get)
        assert "root_id" in sig.parameters

    def test_get_batch_signature(self) -> None:
        sig = inspect.signature(MemoryClient.get_batch)
        assert "requests" in sig.parameters

    def test_list_memories_signature(self) -> None:
        params = inspect.signature(MemoryClient.list_memories).parameters
        for name in ("tags", "indexable", "team_id", "type", "parent_id", "since", "until", "limit"):
            assert name in params
            assert params[name].kind == inspect.Parameter.KEYWORD_ONLY

    def test_update_signature(self) -> None:
        params = inspect.signature(MemoryClient.update).parameters
        assert "memory_id" in params
        for name in ("content", "tags", "metadata", "tags_op", "type"):
            assert name in params
            assert params[name].kind == inspect.Parameter.KEYWORD_ONLY

    def test_existing_search_signature_unchanged(self) -> None:
        params = inspect.signature(MemoryClient.search).parameters
        # The pre-existing surface
        assert {"query", "type", "tags", "since", "until", "limit"} <= set(params)
        # Did NOT add new params to search
        assert "indexable" not in params
        assert "parent_id" not in params

    def test_existing_get_signature_unchanged(self) -> None:
        params = inspect.signature(MemoryClient.get).parameters
        # Only memory_id
        assert "memory_id" in params

    def test_existing_supersede_signature_unchanged(self) -> None:
        params = inspect.signature(MemoryClient.supersede).parameters
        assert {"memory_id", "content"} <= set(params)


# ── Kwarg forwarding to underlying tools ──────────────────────────────────


class TestWriteForwarding:
    """U2, U3, U4, U5 — extended write forwards new kwargs into MemoryWriteInput."""

    async def test_back_compat_call_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        impl = _make_impl()
        captured: dict[str, Any] = {}

        from mem_mcp.mcp.tools import write as write_mod

        new_id = uuid4()

        class _FakeOut:
            id = new_id

        async def _fake_invoke(self: Any, tool: Any, ctx: Any, inp: Any) -> Any:
            captured["inp"] = inp
            return _FakeOut()

        monkeypatch.setattr(MemoryClientImpl, "_invoke_tool", _fake_invoke)
        # Patch isinstance check — _FakeOut isn't a real MemoryWriteOutput
        monkeypatch.setattr(write_mod, "MemoryWriteOutput", _FakeOut)

        result = await impl.write("hello")
        assert result == new_id
        assert captured["inp"].content == "hello"
        assert captured["inp"].parent_id is None
        assert captured["inp"].indexable is True
        assert captured["inp"].references is None

    async def test_forwards_parent_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        impl = _make_impl()
        captured: dict[str, Any] = {}
        parent = uuid4()

        from mem_mcp.mcp.tools import write as write_mod

        class _FakeOut:
            id = uuid4()

        async def _fake_invoke(self: Any, tool: Any, ctx: Any, inp: Any) -> Any:
            captured["inp"] = inp
            return _FakeOut()

        monkeypatch.setattr(MemoryClientImpl, "_invoke_tool", _fake_invoke)
        monkeypatch.setattr(write_mod, "MemoryWriteOutput", _FakeOut)

        # parent_id requires that the call doesn't trigger dedup; we're mocking
        # the underlying tool entirely so dedup doesn't run.
        await impl.write("note content", parent_id=parent)
        assert captured["inp"].parent_id == parent

    async def test_forwards_indexable_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        impl = _make_impl()
        captured: dict[str, Any] = {}

        from mem_mcp.mcp.tools import write as write_mod

        class _FakeOut:
            id = uuid4()

        async def _fake_invoke(self: Any, tool: Any, ctx: Any, inp: Any) -> Any:
            captured["inp"] = inp
            return _FakeOut()

        monkeypatch.setattr(MemoryClientImpl, "_invoke_tool", _fake_invoke)
        monkeypatch.setattr(write_mod, "MemoryWriteOutput", _FakeOut)

        await impl.write("hidden", indexable=False)
        assert captured["inp"].indexable is False


class TestListMemoriesForwarding:
    """U13, U14, U15 — list_memories forwards three filter fields."""

    async def test_forwards_indexable_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        impl = _make_impl()
        captured: dict[str, Any] = {}

        from mem_mcp.mcp.tools import list as list_mod

        class _FakeOut:
            results: ClassVar[list[Any]] = []

        async def _fake_invoke(self: Any, tool: Any, ctx: Any, inp: Any) -> Any:
            captured["inp"] = inp
            return _FakeOut()

        monkeypatch.setattr(MemoryClientImpl, "_invoke_tool", _fake_invoke)
        monkeypatch.setattr(list_mod, "MemoryListOutput", _FakeOut)

        await impl.list_memories(indexable=False)
        assert captured["inp"].indexable is False

    async def test_forwards_team_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        impl = _make_impl()
        captured: dict[str, Any] = {}
        team = uuid4()

        from mem_mcp.mcp.tools import list as list_mod

        class _FakeOut:
            results: ClassVar[list[Any]] = []

        async def _fake_invoke(self: Any, tool: Any, ctx: Any, inp: Any) -> Any:
            captured["inp"] = inp
            return _FakeOut()

        monkeypatch.setattr(MemoryClientImpl, "_invoke_tool", _fake_invoke)
        monkeypatch.setattr(list_mod, "MemoryListOutput", _FakeOut)

        await impl.list_memories(team_id=team)
        assert captured["inp"].team_id == team

    async def test_forwards_parent_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        impl = _make_impl()
        captured: dict[str, Any] = {}
        parent = uuid4()

        from mem_mcp.mcp.tools import list as list_mod

        class _FakeOut:
            results: ClassVar[list[Any]] = []

        async def _fake_invoke(self: Any, tool: Any, ctx: Any, inp: Any) -> Any:
            captured["inp"] = inp
            return _FakeOut()

        monkeypatch.setattr(MemoryClientImpl, "_invoke_tool", _fake_invoke)
        monkeypatch.setattr(list_mod, "MemoryListOutput", _FakeOut)

        await impl.list_memories(parent_id=parent)
        assert captured["inp"].parent_id == parent


# ── update type-restriction ───────────────────────────────────────────────


class TestUpdateTypeRestriction:
    """U17, U18, U19 — Option (A) SDK-layer type-restriction enforcement."""

    async def test_rejects_content_on_decision(self, monkeypatch: pytest.MonkeyPatch) -> None:
        impl = _make_impl()
        memory_id = uuid4()
        get_called = MagicMock()
        update_called = MagicMock()

        class _FakeMemory:
            type = "decision"

        class _FakeGetOut:
            memory = _FakeMemory()

        from mem_mcp.mcp.tools import get as get_mod

        async def _fake_invoke(self: Any, tool: Any, ctx: Any, inp: Any) -> Any:
            cls_name = tool.__class__.__name__
            if cls_name == "MemoryGetTool":
                get_called(inp)
                return _FakeGetOut()
            if cls_name == "MemoryUpdateTool":
                update_called(inp)
                return MagicMock()
            raise AssertionError(f"unexpected tool: {cls_name}")

        monkeypatch.setattr(MemoryClientImpl, "_invoke_tool", _fake_invoke)
        monkeypatch.setattr(get_mod, "MemoryGetOutput", _FakeGetOut)

        with pytest.raises(PluginValidationError) as ei:
            await impl.update(memory_id, content="new")
        assert ei.value.code == "update_not_allowed_for_type"
        assert "decision" in ei.value.data["type"]
        get_called.assert_called_once()
        update_called.assert_not_called()

    async def test_allows_tags_on_decision_no_prefetch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """U18 — tags-only edit on decision: succeeds, NO pre-fetch (cost discipline)."""
        impl = _make_impl()
        memory_id = uuid4()
        get_called = MagicMock()
        update_called = MagicMock()

        from mem_mcp.mcp.tools import update as update_mod

        class _FakeUpdateOut:
            def model_dump(self, mode: str = "json") -> dict[str, Any]:
                return {"id": str(memory_id), "is_new_version": False}

        async def _fake_invoke(self: Any, tool: Any, ctx: Any, inp: Any) -> Any:
            cls_name = tool.__class__.__name__
            if cls_name == "MemoryGetTool":
                get_called(inp)
                raise AssertionError("MemoryGetTool should NOT be called when content is None")
            if cls_name == "MemoryUpdateTool":
                update_called(inp)
                return _FakeUpdateOut()
            raise AssertionError(f"unexpected tool: {cls_name}")

        monkeypatch.setattr(MemoryClientImpl, "_invoke_tool", _fake_invoke)
        monkeypatch.setattr(update_mod, "MemoryUpdateOutput", _FakeUpdateOut)

        result = await impl.update(memory_id, tags=["new-tag"], tags_op="add")
        assert result["id"] == str(memory_id)
        get_called.assert_not_called()
        update_called.assert_called_once()

    async def test_allows_content_on_note(self, monkeypatch: pytest.MonkeyPatch) -> None:
        impl = _make_impl()
        memory_id = uuid4()

        class _FakeMemory:
            type = "note"

        class _FakeGetOut:
            memory = _FakeMemory()

        class _FakeUpdateOut:
            def model_dump(self, mode: str = "json") -> dict[str, Any]:
                return {"id": str(memory_id), "is_new_version": False, "content_changed": True}

        from mem_mcp.mcp.tools import get as get_mod
        from mem_mcp.mcp.tools import update as update_mod

        update_called = MagicMock()

        async def _fake_invoke(self: Any, tool: Any, ctx: Any, inp: Any) -> Any:
            cls_name = tool.__class__.__name__
            if cls_name == "MemoryGetTool":
                return _FakeGetOut()
            if cls_name == "MemoryUpdateTool":
                update_called(inp)
                return _FakeUpdateOut()
            raise AssertionError(f"unexpected tool: {cls_name}")

        monkeypatch.setattr(MemoryClientImpl, "_invoke_tool", _fake_invoke)
        monkeypatch.setattr(get_mod, "MemoryGetOutput", _FakeGetOut)
        monkeypatch.setattr(update_mod, "MemoryUpdateOutput", _FakeUpdateOut)

        result = await impl.update(memory_id, content="updated")
        update_called.assert_called_once()
        assert result["is_new_version"] is False


# ── get() back-compat carve-out ────────────────────────────────────────────


class TestGetBackCompat:
    """U27 — get() returns None on not-found, NOT PluginValidationError."""

    async def test_get_returns_none_on_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        impl = _make_impl()
        missing_id = uuid4()

        # Replace MemoryGetTool's __call__ to raise the not-found JsonRpcError
        from mem_mcp.mcp.tools import get as get_mod

        async def _raise_not_found(self: Any, ctx: Any, inp: Any) -> Any:
            raise JsonRpcError(-32602, "memory not found", data={})

        monkeypatch.setattr(get_mod.MemoryGetTool, "__call__", _raise_not_found)

        # get() does NOT use _invoke_tool, so we should see the special-case None
        result = await impl.get(missing_id)
        assert result is None

    async def test_get_raises_on_other_jsonrpc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get's not-None error path: re-raises non-not-found JsonRpcError as-is."""
        impl = _make_impl()

        from mem_mcp.mcp.tools import get as get_mod

        async def _raise_other(self: Any, ctx: Any, inp: Any) -> Any:
            raise JsonRpcError(-32603, "internal error", data={})

        monkeypatch.setattr(get_mod.MemoryGetTool, "__call__", _raise_other)

        with pytest.raises(JsonRpcError) as ei:
            await impl.get(uuid4())
        assert ei.value.code == -32603
