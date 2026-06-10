"""Tests for NoticeClientImpl payload serialization.

Plugin payloads routinely carry datetimes and UUIDs (fire_at, item ids).
A bare json.dumps raises TypeError on those and fails the whole queue()
call — the same bug class that broke reminders dismiss/resolve in the
plugin repo (mem-mcp-skill-reminders repository.py record_event).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from mem_mcp.plugins.clients.notices import NoticeClientImpl, _json_default


class _CaptureConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((query, args))
        return "INSERT 0 1"


def _patch_system_tx(monkeypatch: pytest.MonkeyPatch, conn: _CaptureConn) -> None:
    @asynccontextmanager
    async def fake_tx(pool: Any) -> Any:
        yield conn

    monkeypatch.setattr("mem_mcp.plugins.clients.notices.system_tx", fake_tx)


class TestJsonDefault:
    def test_datetime_isoformat(self) -> None:
        dt = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)
        assert _json_default(dt) == "2026-06-10T12:00:00+00:00"

    def test_date_isoformat(self) -> None:
        assert _json_default(date(2026, 6, 10)) == "2026-06-10"

    def test_uuid_stringified(self) -> None:
        u = uuid4()
        assert _json_default(u) == str(u)


class TestNoticeClientQueue:
    @pytest.mark.asyncio
    async def test_datetime_payload_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _CaptureConn()
        _patch_system_tx(monkeypatch, conn)
        client = NoticeClientImpl(MagicMock(), uuid4(), "reminders")

        fire_at = datetime(2026, 6, 10, 11, 41, 38, tzinfo=UTC)
        item_id = uuid4()
        await client.queue(
            "reminder_fired",
            "Reminder: {content}",
            {"content": "Take medicines", "fire_at": fire_at, "item_id": item_id},
        )

        assert len(conn.calls) == 1
        _, args = conn.calls[0]
        payload_json = args[4]
        decoded = json.loads(payload_json)
        assert decoded["fire_at"] == fire_at.isoformat()
        assert decoded["item_id"] == str(item_id)
        assert decoded["content"] == "Take medicines"

    @pytest.mark.asyncio
    async def test_plain_payload_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _CaptureConn()
        _patch_system_tx(monkeypatch, conn)
        client = NoticeClientImpl(MagicMock(), uuid4(), "p")

        await client.queue("k", "t", {"a": 1, "b": "x"})

        _, args = conn.calls[0]
        assert json.loads(args[4]) == {"a": 1, "b": "x"}

    @pytest.mark.asyncio
    async def test_queue_params_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _CaptureConn()
        _patch_system_tx(monkeypatch, conn)
        tenant: UUID = uuid4()
        client = NoticeClientImpl(MagicMock(), tenant, "reminders")

        await client.queue("kind", "tmpl", {}, ttl_seconds=60)

        _, args = conn.calls[0]
        assert args[0] == tenant
        assert args[1] == "reminders"
        assert args[2] == "kind"
        assert args[3] == "tmpl"
        assert args[5] == "60"
