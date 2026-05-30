"""Schema validation tests for MemoryGetBatchInput / Entry / Output."""

from __future__ import annotations

from uuid import uuid4

import pytest

from mem_mcp.mcp.tools.get_batch import (
    MemoryGetBatchEntry,
    MemoryGetBatchInput,
    MemoryGetBatchResultEntry,
)


class TestEntryShape:
    def test_id_alone_valid(self) -> None:
        e = MemoryGetBatchEntry(id=uuid4())
        assert e.id is not None
        assert e.team_id is None

    def test_slug_tuple_valid(self) -> None:
        e = MemoryGetBatchEntry(team_id=uuid4(), resource_type="decision", slug="my-slug")
        assert e.slug == "my-slug"
        assert e.id is None

    def test_both_shapes_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot provide both"):
            MemoryGetBatchEntry(id=uuid4(), team_id=uuid4(), resource_type="decision", slug="x")

    def test_neither_shape_rejected(self) -> None:
        with pytest.raises(ValueError, match="must provide either"):
            MemoryGetBatchEntry()

    def test_partial_slug_tuple_rejected(self) -> None:
        with pytest.raises(ValueError, match="must provide either"):
            MemoryGetBatchEntry(team_id=uuid4(), resource_type="decision")

    def test_slug_length_validation(self) -> None:
        too_long = "a" * 65
        with pytest.raises(ValueError, match="slug must be 1-64"):
            MemoryGetBatchEntry(team_id=uuid4(), resource_type="fact", slug=too_long)

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValueError):
            MemoryGetBatchEntry(id=uuid4(), unknown_field="x")  # type: ignore[call-arg]


class TestBatchInput:
    def test_minimum_one_entry(self) -> None:
        with pytest.raises(ValueError):
            MemoryGetBatchInput(requests=[])

    def test_cap_at_fifty(self) -> None:
        entries = [MemoryGetBatchEntry(id=uuid4()) for _ in range(50)]
        inp = MemoryGetBatchInput(requests=entries)
        assert len(inp.requests) == 50

    def test_over_cap_rejected(self) -> None:
        entries = [MemoryGetBatchEntry(id=uuid4()) for _ in range(51)]
        with pytest.raises(ValueError):
            MemoryGetBatchInput(requests=entries)

    def test_duplicate_ids_allowed(self) -> None:
        same = uuid4()
        inp = MemoryGetBatchInput(
            requests=[MemoryGetBatchEntry(id=same), MemoryGetBatchEntry(id=same)]
        )
        assert inp.requests[0].id == inp.requests[1].id


class TestResultEntry:
    def test_success_shape(self) -> None:
        # ok=True with memory (memory shape validated separately)
        e = MemoryGetBatchResultEntry(ok=True, memory=None)
        assert e.ok is True

    def test_error_envelope_keys(self) -> None:
        e = MemoryGetBatchResultEntry(
            ok=False,
            error={
                "code": "memory_not_accessible",
                "message": "memory not found or not accessible",
            },
        )
        assert e.error is not None
        assert e.error["code"] == "memory_not_accessible"

    def test_error_extra_keys_rejected(self) -> None:
        with pytest.raises(ValueError, match="unexpected keys"):
            MemoryGetBatchResultEntry(ok=False, error={"code": "x", "message": "y", "extra": "z"})
