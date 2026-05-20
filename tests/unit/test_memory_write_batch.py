"""Tests for memory_write_batch tool."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mem_mcp.mcp.tools.write import MemoryWriteInput
from mem_mcp.mcp.tools.write_batch import MemoryWriteBatchInput

# --------------------------------------------------------------------------
# MemoryWriteBatchInput validation tests
# --------------------------------------------------------------------------


class TestMemoryWriteBatchInput:
    """Validate input model constraints."""

    def test_minimal_valid(self) -> None:
        """Minimal valid batch: single entry."""
        entries = [MemoryWriteInput(content="hello")]
        inp = MemoryWriteBatchInput(memories=entries)
        assert len(inp.memories) == 1
        assert inp.on_error == "continue"

    def test_max_batch_size(self) -> None:
        """Max 200 entries allowed."""
        entries = [MemoryWriteInput(content=f"msg-{i}") for i in range(200)]
        inp = MemoryWriteBatchInput(memories=entries)
        assert len(inp.memories) == 200

    def test_exceeds_max_batch_size(self) -> None:
        """201 entries should fail validation."""
        entries = [MemoryWriteInput(content=f"msg-{i}") for i in range(201)]
        with pytest.raises(ValidationError) as exc_info:
            MemoryWriteBatchInput(memories=entries)
        assert "too_long" in str(exc_info.value)

    def test_empty_batch(self) -> None:
        """Empty batch should fail validation."""
        with pytest.raises(ValidationError) as exc_info:
            MemoryWriteBatchInput(memories=[])
        assert "too_short" in str(exc_info.value)

    def test_on_error_continue(self) -> None:
        """on_error='continue' is valid."""
        entries = [MemoryWriteInput(content="test")]
        inp = MemoryWriteBatchInput(memories=entries, on_error="continue")
        assert inp.on_error == "continue"

    def test_on_error_fail_all(self) -> None:
        """on_error='fail_all' is valid."""
        entries = [MemoryWriteInput(content="test")]
        inp = MemoryWriteBatchInput(memories=entries, on_error="fail_all")
        assert inp.on_error == "fail_all"

    def test_invalid_on_error(self) -> None:
        """Invalid on_error value should fail."""
        entries = [MemoryWriteInput(content="test")]
        with pytest.raises(ValidationError):
            MemoryWriteBatchInput(memories=entries, on_error="invalid")  # type: ignore[arg-type]

    def test_each_entry_independently_validated(self) -> None:
        """Invalid memory entry should be caught at MemoryWriteInput validation."""
        with pytest.raises(ValidationError):
            MemoryWriteInput(content="x" * 200_001)  # Exceeds max

        # Once created, they can be batched
        entries = [
            MemoryWriteInput(content="good1"),
            MemoryWriteInput(content="good2"),
        ]
        inp = MemoryWriteBatchInput(memories=entries)
        assert len(inp.memories) == 2

    def test_extra_fields_forbidden(self) -> None:
        """Extra fields in input should be rejected."""
        entries = [MemoryWriteInput(content="test")]
        with pytest.raises(ValidationError):
            MemoryWriteBatchInput(
                memories=entries,
                on_error="continue",
                unknown_field="should fail",  # type: ignore[call-arg]
            )

    def test_batch_with_varied_types(self) -> None:
        """Batch can contain varied memory types."""
        entries = [
            MemoryWriteInput(content="decision", type="decision"),
            MemoryWriteInput(content="fact", type="fact"),
            MemoryWriteInput(content="snippet", type="snippet"),
            MemoryWriteInput(content="note", type="note"),
            MemoryWriteInput(content="question", type="question"),
        ]
        inp = MemoryWriteBatchInput(memories=entries)
        assert len(inp.memories) == 5
        assert inp.memories[0].type == "decision"
        assert inp.memories[1].type == "fact"

    def test_batch_with_varied_tags(self) -> None:
        """Each entry can have independent tag sets."""
        entries = [
            MemoryWriteInput(content="a", tags=["x", "y"]),
            MemoryWriteInput(content="b", tags=["z"]),
            MemoryWriteInput(content="c", tags=[]),
        ]
        inp = MemoryWriteBatchInput(memories=entries)
        assert inp.memories[0].tags == ["x", "y"]
        assert inp.memories[1].tags == ["z"]
        assert inp.memories[2].tags == []

    def test_batch_with_varied_indexable(self) -> None:
        """Each entry can independently set indexable."""
        entries = [
            MemoryWriteInput(content="indexed", indexable=True),
            MemoryWriteInput(content="private", indexable=False),
        ]
        inp = MemoryWriteBatchInput(memories=entries)
        assert inp.memories[0].indexable is True
        assert inp.memories[1].indexable is False

    def test_batch_default_on_error_is_continue(self) -> None:
        """Default on_error behavior is 'continue', not 'fail_all'."""
        entries = [MemoryWriteInput(content="test")]
        inp = MemoryWriteBatchInput(memories=entries)
        assert inp.on_error == "continue"
