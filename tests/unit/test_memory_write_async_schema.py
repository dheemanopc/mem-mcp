"""Schema validation tests for memory_write_async output + ESTIMATED_CONSISTENCY_SECONDS."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from mem_mcp.mcp.tools.write_async import (
    ESTIMATED_CONSISTENCY_SECONDS,
    MemoryWriteAsyncOutput,
)


class TestAsyncOutput:
    def test_shape(self) -> None:
        now = datetime.now(UTC)
        out = MemoryWriteAsyncOutput(
            request_id=uuid4(),
            queued_at=now,
            estimated_consistency_by=now + timedelta(seconds=ESTIMATED_CONSISTENCY_SECONDS),
        )
        assert out.estimated_consistency_by > out.queued_at

    def test_consistency_window_default(self) -> None:
        # 5s per blessed Decision 4 in proposal memsys:80b04691.
        assert ESTIMATED_CONSISTENCY_SECONDS == 5
