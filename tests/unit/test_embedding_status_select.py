"""Regression tests for embedding_status column in SELECT statements.

Bug: PR #225 added embedding_status to MemoryRecord, ThreadMemoryRecord, and
MemoryListItem response models but did NOT include the column in the SELECT
statements of memory_get, memory_thread_get, and memory_list handlers. This
caused Pydantic validation errors when the row dict was missing the required
embedding_status field.

These tests verify that:
1. embedding_status is in the SELECT statements (via string inspection)
2. The Pydantic models require embedding_status (validation tests)
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mem_mcp.mcp.tools.get import MemoryGetTool, MemoryRecord
from mem_mcp.mcp.tools.list import MemoryListItem, MemoryListTool
from mem_mcp.mcp.tools.thread_get import MemoryThreadGetTool, ThreadMemoryRecord


def test_memory_get_select_includes_embedding_status() -> None:
    """Verify memory_get SELECT statement includes embedding_status column.

    This is a regression test for PR #225 where embedding_status was added to
    the MemoryRecord model but forgotten in the SELECT statement.
    """
    tool = MemoryGetTool()
    source = inspect.getsource(tool.__call__)

    # Check for embedding_status in the source code
    # It should appear in the SELECT statement(s), not just in docstrings
    assert (
        "embedding_status" in source
    ), "memory_get tool must include embedding_status in SELECT statement"

    # Ensure it's not just in a comment but actually in the SQL
    sql_relevant = "\n".join(
        [line for line in source.split("\n") if "SELECT" in line or "embedding_status" in line]
    )
    assert "embedding_status" in sql_relevant


def test_memory_thread_get_select_includes_embedding_status() -> None:
    """Verify memory_thread_get SELECT statements include embedding_status column."""
    tool = MemoryThreadGetTool()
    source = inspect.getsource(tool.__call__)

    # Check for embedding_status in SELECT statements
    assert (
        "embedding_status" in source
    ), "memory_thread_get tool must include embedding_status in SELECT statements"

    # More specific check: ensure it appears multiple times (root + replies)
    count = source.count("embedding_status")
    assert (
        count >= 2
    ), f"memory_thread_get must have embedding_status in multiple SELECT statements, found {count}"


def test_memory_list_select_includes_embedding_status() -> None:
    """Verify memory_list SELECT statement includes embedding_status column."""
    tool = MemoryListTool()
    source = inspect.getsource(tool.__call__)

    # Check for embedding_status in SELECT statement
    assert (
        "embedding_status" in source
    ), "memory_list tool must include embedding_status in SELECT statement"


def test_memory_record_requires_embedding_status() -> None:
    """Verify MemoryRecord Pydantic model requires embedding_status field."""
    # This should fail validation because embedding_status is missing
    try:
        MemoryRecord(  # type: ignore[call-arg]
            id=uuid4(),
            content="test",
            type="note",
            tags=[],
            metadata={},
            version=1,
            is_current=True,
            supersedes=None,
            superseded_by=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            deleted_at=None,
            expires_at=None,
            indexable=True,
            # embedding_status is intentionally omitted
        )
        pytest.fail("Expected ValidationError for missing embedding_status")
    except Exception as e:
        # Should be a validation error
        assert "embedding_status" in str(e).lower() or "field required" in str(e).lower()


def test_thread_memory_record_requires_embedding_status() -> None:
    """Verify ThreadMemoryRecord Pydantic model requires embedding_status field."""
    # This should fail validation because embedding_status is missing
    try:
        ThreadMemoryRecord(  # type: ignore[call-arg]
            id=uuid4(),
            content="test",
            type="note",
            tags=[],
            metadata={},
            version=1,
            is_current=True,
            parent_id=None,
            supersedes=None,
            superseded_by=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            deleted_at=None,
            # embedding_status is intentionally omitted
        )
        pytest.fail("Expected ValidationError for missing embedding_status")
    except Exception as e:
        # Should be a validation error
        assert "embedding_status" in str(e).lower() or "field required" in str(e).lower()


def test_memory_list_item_requires_embedding_status() -> None:
    """Verify MemoryListItem Pydantic model requires embedding_status field."""
    # This should fail validation because embedding_status is missing
    try:
        MemoryListItem(  # type: ignore[call-arg]
            id=uuid4(),
            content="test",
            preview="test",
            type="note",
            tags=[],
            version=1,
            is_current=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            deleted_at=None,
            expires_at=None,
            indexable=True,
            # embedding_status is intentionally omitted
        )
        pytest.fail("Expected ValidationError for missing embedding_status")
    except Exception as e:
        # Should be a validation error
        assert "embedding_status" in str(e).lower() or "field required" in str(e).lower()


def test_thread_get_select_includes_indexable() -> None:
    """thread_get.py SELECTs must include `indexable` to populate the response field."""
    tool = MemoryThreadGetTool()
    source = inspect.getsource(tool.__call__)
    # Check that indexable appears at least twice (root + replies)
    indexable_count = source.count("indexable")
    assert (
        indexable_count >= 2
    ), f"thread_get.py must include indexable in at least 2 SELECTs (found {indexable_count})"


def test_search_select_includes_indexable() -> None:
    """search.py SELECTs must include `indexable`."""
    # The search uses hybrid_query.py, check that module
    from mem_mcp.memory.hybrid_query import _HYBRID_SQL

    assert "indexable" in _HYBRID_SQL, "hybrid_query.py SELECT must include indexable column"


def test_list_select_includes_indexable() -> None:
    """list.py SELECT must include `indexable`."""
    tool = MemoryListTool()
    source = inspect.getsource(tool.__call__)
    assert "indexable" in source, "list.py must SELECT indexable"
