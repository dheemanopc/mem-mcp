"""T8 Tier-2 SDK extension — unit tests.

Per Test Plan v1 `53cf3015` + v2 delta `894cea7a`. Covers six SDK additions:
- refs_in / refs_out (item #1)
- write += slug_clue (item #2)
- slug_lookup (item #3)
- write_async (item #4)
- write_batch (item #5)
- write += expires_at / ttl_seconds (item #6)

Plus anti-regression on the OUT-1 line (team_id/visibility NOT on write).

Note: DA spec's `refs_in/out limit` param does not exist in substrate
(verified at HEAD); SDK signature matches substrate truth. Tests cover
the substrate-truth shape.
"""

from __future__ import annotations

import inspect
from typing import Any, ClassVar
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from mem_mcp.plugins.clients.memory import MemoryClientImpl
from mem_mcp.plugins.contract import MemoryClient, PluginValidationError


def _make_impl() -> MemoryClientImpl:
    pool = MagicMock()
    deps = MagicMock()
    return MemoryClientImpl(
        pool=pool,
        tenant_id=uuid4(),
        plugin_id="test-plugin",
        deps=deps,
        identity_id=uuid4(),
    )


# ── Item #1: refs_in / refs_out ────────────────────────────────────────────


class TestRefsInOut:
    def test_refs_in_signature(self) -> None:
        params = inspect.signature(MemoryClient.refs_in).parameters
        assert "memory_id" in params
        # limit dropped per substrate truth (not in RefsInInput)
        assert "limit" not in params

    def test_refs_out_signature(self) -> None:
        params = inspect.signature(MemoryClient.refs_out).parameters
        assert "memory_id" in params
        assert "limit" not in params

    async def test_refs_in_forwards_memory_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        impl = _make_impl()
        captured: dict[str, Any] = {}
        mid = uuid4()

        from mem_mcp.mcp.tools import refs_in as refs_in_mod

        class _FakeOut:
            refs: ClassVar[list[Any]] = []
            accessible_count: int = 0
            inaccessible_count: int = 0

        async def _fake_invoke(self: Any, tool: Any, ctx: Any, inp: Any) -> Any:
            captured["inp"] = inp
            return _FakeOut()

        monkeypatch.setattr(MemoryClientImpl, "_invoke_tool", _fake_invoke)
        monkeypatch.setattr(refs_in_mod, "RefsInOutput", _FakeOut)

        result = await impl.refs_in(mid)
        assert captured["inp"].memory_id == mid
        assert result == []

    async def test_refs_out_forwards_memory_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        impl = _make_impl()
        captured: dict[str, Any] = {}
        mid = uuid4()

        from mem_mcp.mcp.tools import refs_out as refs_out_mod

        class _FakeOut:
            refs: ClassVar[list[Any]] = []
            accessible_count: int = 0
            inaccessible_count: int = 0

        async def _fake_invoke(self: Any, tool: Any, ctx: Any, inp: Any) -> Any:
            captured["inp"] = inp
            return _FakeOut()

        monkeypatch.setattr(MemoryClientImpl, "_invoke_tool", _fake_invoke)
        monkeypatch.setattr(refs_out_mod, "RefsOutOutput", _FakeOut)

        result = await impl.refs_out(mid)
        assert captured["inp"].memory_id == mid
        assert result == []

    async def test_refs_in_returns_refs_field_verbatim(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SDK returns out.refs (NOT out.results) per DELTA 1 + DELTA 2."""
        impl = _make_impl()

        from mem_mcp.mcp.tools import refs_in as refs_in_mod

        class _FakeRef:
            def model_dump(self, mode: str = "json") -> dict[str, Any]:
                return {"source_memory_id": "abc", "reference_kind": "derived-from"}

        class _FakeOut:
            refs: ClassVar[list[Any]] = [_FakeRef(), _FakeRef(), _FakeRef()]
            accessible_count: int = 3
            inaccessible_count: int = 2  # Should NOT be in SDK output

        async def _fake_invoke(self: Any, tool: Any, ctx: Any, inp: Any) -> Any:
            return _FakeOut()

        monkeypatch.setattr(MemoryClientImpl, "_invoke_tool", _fake_invoke)
        monkeypatch.setattr(refs_in_mod, "RefsInOutput", _FakeOut)

        result = await impl.refs_in(uuid4())
        assert len(result) == 3  # All accessible edges
        # Critical: aggregate counts MUST NOT be in the SDK output (IT-08 leak guard)
        for entry in result:
            assert "accessible_count" not in entry
            assert "inaccessible_count" not in entry


# ── Item #2: slug_clue on write ────────────────────────────────────────────


class TestSlugClueOnWrite:
    def test_write_signature_includes_slug_clue(self) -> None:
        params = inspect.signature(MemoryClient.write).parameters
        assert "slug_clue" in params
        assert params["slug_clue"].kind == inspect.Parameter.KEYWORD_ONLY

    async def test_write_forwards_slug_clue(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

        await impl.write("decision content", type="decision", slug_clue="my-slug")
        assert captured["inp"].slug_clue == "my-slug"

    async def test_write_slug_clue_on_note_raises_plugin_validation_error(self) -> None:
        """SF-T2-F + F-9: substrate's _validate_slug_clue rejects slug_clue
        on non-VERSIONED_TYPES; SDK converts to PluginValidationError."""
        impl = _make_impl()

        with pytest.raises(PluginValidationError) as ei:
            await impl.write("note content", type="note", slug_clue="my-slug")
        assert ei.value.code == "invalid_params"
        assert "slug_clue" in str(ei.value.data)


# ── Item #3: slug_lookup ──────────────────────────────────────────────────


class TestSlugLookup:
    def test_signature(self) -> None:
        params = inspect.signature(MemoryClient.slug_lookup).parameters
        for name in ("team_id", "resource_type", "slug"):
            assert name in params
            assert params[name].kind == inspect.Parameter.KEYWORD_ONLY

    async def test_resolvable_returns_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        impl = _make_impl()
        from datetime import UTC, datetime

        from mem_mcp.mcp.tools import slug_lookup as slug_mod

        target_id = uuid4()

        class _FakeOut:
            memory_id = target_id
            title = "Some decision"
            updated_at = datetime.now(UTC)

            def model_dump(self, mode: str = "json") -> dict[str, Any]:
                return {"memory_id": str(target_id), "title": "Some decision"}

        async def _fake_invoke(self: Any, tool: Any, ctx: Any, inp: Any) -> Any:
            return _FakeOut()

        monkeypatch.setattr(MemoryClientImpl, "_invoke_tool", _fake_invoke)
        monkeypatch.setattr(slug_mod, "MemsysSlugLookupOutput", _FakeOut)

        result = await impl.slug_lookup(team_id=uuid4(), resource_type="decision", slug="x")
        assert result is not None
        assert result["memory_id"] == str(target_id)

    async def test_all_null_trio_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """IT-08 opaque collapse: substrate returns (None, None, None) on
        either not-found OR no-access; SDK returns None opaquely."""
        impl = _make_impl()
        from mem_mcp.mcp.tools import slug_lookup as slug_mod

        class _FakeOut:
            memory_id = None
            title = None
            updated_at = None

        async def _fake_invoke(self: Any, tool: Any, ctx: Any, inp: Any) -> Any:
            return _FakeOut()

        monkeypatch.setattr(MemoryClientImpl, "_invoke_tool", _fake_invoke)
        monkeypatch.setattr(slug_mod, "MemsysSlugLookupOutput", _FakeOut)

        result = await impl.slug_lookup(team_id=uuid4(), resource_type="decision", slug="x")
        assert result is None


# ── Item #4: write_async ──────────────────────────────────────────────────


class TestWriteAsync:
    def test_signature_mirrors_write(self) -> None:
        write_params = set(inspect.signature(MemoryClient.write).parameters)
        async_params = set(inspect.signature(MemoryClient.write_async).parameters)
        # write_async should have all the write kwargs (excluding self)
        for k in write_params - {"self"}:
            assert k in async_params, f"write_async missing kwarg: {k}"

    def test_no_ryow_documented(self) -> None:
        """SF-T2-G + DA footgun directive — docstring must state no-RYOW."""
        doc = inspect.getdoc(MemoryClient.write_async) or ""
        # Must mention RYOW limitation in some form
        assert "read-your-own-write" in doc.lower() or "ryow" in doc.lower()

    async def test_returns_envelope_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        impl = _make_impl()
        from datetime import UTC, datetime

        from mem_mcp.mcp.tools import write_async as wa_mod

        req_id = uuid4()
        now = datetime.now(UTC)

        class _FakeOut:
            request_id = req_id
            queued_at = now
            estimated_consistency_by = now

            def model_dump(self, mode: str = "json") -> dict[str, Any]:
                return {
                    "request_id": str(req_id),
                    "queued_at": now.isoformat(),
                    "estimated_consistency_by": now.isoformat(),
                }

        async def _fake_invoke(self: Any, tool: Any, ctx: Any, inp: Any) -> Any:
            return _FakeOut()

        monkeypatch.setattr(MemoryClientImpl, "_invoke_tool", _fake_invoke)
        monkeypatch.setattr(wa_mod, "MemoryWriteAsyncOutput", _FakeOut)

        result = await impl.write_async("hello")
        assert "request_id" in result
        assert "queued_at" in result
        assert "estimated_consistency_by" in result


# ── Item #5: write_batch ──────────────────────────────────────────────────


class TestWriteBatch:
    def test_signature(self) -> None:
        params = inspect.signature(MemoryClient.write_batch).parameters
        assert "memories" in params
        assert "on_error" in params
        assert params["on_error"].kind == inspect.Parameter.KEYWORD_ONLY

    async def test_returns_per_entry_array(self, monkeypatch: pytest.MonkeyPatch) -> None:
        impl = _make_impl()
        from mem_mcp.mcp.tools import write_batch as wb_mod

        class _FakeEntry:
            def model_dump(self, mode: str = "json") -> dict[str, Any]:
                return {"ok": True, "id": str(uuid4())}

        class _FakeOut:
            results: ClassVar[list[Any]] = [_FakeEntry(), _FakeEntry(), _FakeEntry()]

        async def _fake_invoke(self: Any, tool: Any, ctx: Any, inp: Any) -> Any:
            return _FakeOut()

        monkeypatch.setattr(MemoryClientImpl, "_invoke_tool", _fake_invoke)
        monkeypatch.setattr(wb_mod, "MemoryWriteBatchOutput", _FakeOut)

        result = await impl.write_batch([{"content": "a"}, {"content": "b"}, {"content": "c"}])
        assert len(result) == 3
        assert all(r["ok"] for r in result)

    async def test_empty_list_rejected(self) -> None:
        """DELTA 3 bound check: substrate min_length=1."""
        impl = _make_impl()
        with pytest.raises(PluginValidationError) as ei:
            await impl.write_batch([])
        assert ei.value.code == "invalid_params"

    async def test_oversize_201_rejected(self) -> None:
        """DELTA 3 bound check: substrate max_length=200."""
        impl = _make_impl()
        oversize = [{"content": f"m{i}"} for i in range(201)]
        with pytest.raises(PluginValidationError) as ei:
            await impl.write_batch(oversize)
        assert ei.value.code == "invalid_params"


# ── Item #6: expires_at + ttl_seconds ─────────────────────────────────────


class TestExpiresAtTtlSeconds:
    def test_write_signature_includes_both(self) -> None:
        params = inspect.signature(MemoryClient.write).parameters
        assert "expires_at" in params
        assert "ttl_seconds" in params

    def test_write_async_signature_includes_both(self) -> None:
        params = inspect.signature(MemoryClient.write_async).parameters
        assert "expires_at" in params
        assert "ttl_seconds" in params

    async def test_forwards_ttl_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

        await impl.write("hello", ttl_seconds=3600)
        assert captured["inp"].ttl_seconds == 3600

    async def test_mutual_exclusion(self) -> None:
        """Substrate's _validate_expiry_conflict rejects both set."""
        from datetime import UTC, datetime, timedelta

        impl = _make_impl()
        with pytest.raises(PluginValidationError) as ei:
            await impl.write(
                "hello",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                ttl_seconds=3600,
            )
        assert ei.value.code == "invalid_params"


# ── Anti-regression on OUT-1 (team_id / visibility NOT on Protocol) ───────


class TestOut1Hardening:
    def test_write_does_not_include_team_id(self) -> None:
        assert "team_id" not in inspect.signature(MemoryClient.write).parameters

    def test_write_does_not_include_visibility(self) -> None:
        assert "visibility" not in inspect.signature(MemoryClient.write).parameters

    def test_write_async_does_not_include_team_id(self) -> None:
        assert "team_id" not in inspect.signature(MemoryClient.write_async).parameters

    def test_write_async_does_not_include_visibility(self) -> None:
        assert "visibility" not in inspect.signature(MemoryClient.write_async).parameters
