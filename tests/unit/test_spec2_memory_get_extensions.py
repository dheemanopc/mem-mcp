"""Tests for memory_get extensions (Spec 2 stage B): slug-based lookup."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from asyncpg import Pool  # type: ignore[import-untyped]

pytestmark = pytest.mark.asyncio


class TestMemoryGetInput:
    """Tests for memory_get input validation."""

    async def test_id_alone_valid(self, pg_pool: Pool) -> None:
        """memory_get with id alone is valid."""
        from mem_mcp.mcp.tools.get import MemoryGetInput

        mem_id = uuid4()
        inp = MemoryGetInput(id=mem_id, include_history=False)
        assert inp.id == mem_id
        assert inp.team_id is None
        assert inp.resource_type is None
        assert inp.slug is None

    async def test_slug_tuple_valid(self, pg_pool: Pool) -> None:
        """memory_get with (team_id, resource_type, slug) is valid."""
        from mem_mcp.mcp.tools.get import MemoryGetInput

        team_id = uuid4()
        inp = MemoryGetInput(
            team_id=team_id,
            resource_type="decision",
            slug="my-decision",
        )
        assert inp.id is None
        assert inp.team_id == team_id
        assert inp.resource_type == "decision"
        assert inp.slug == "my-decision"

    async def test_both_id_and_slug_rejects(self, pg_pool: Pool) -> None:
        """memory_get with both id and slug tuple rejects."""
        from mem_mcp.mcp.tools.get import MemoryGetInput

        with pytest.raises(ValueError, match="cannot provide both"):
            MemoryGetInput(
                id=uuid4(),
                team_id=uuid4(),
                resource_type="decision",
                slug="my-decision",
            )

    async def test_neither_id_nor_slug_rejects(self, pg_pool: Pool) -> None:
        """memory_get with neither id nor slug tuple rejects."""
        from mem_mcp.mcp.tools.get import MemoryGetInput

        with pytest.raises(ValueError, match="must provide either id"):
            MemoryGetInput()

    async def test_partial_slug_tuple_rejects(self, pg_pool: Pool) -> None:
        """memory_get with partial slug tuple rejects."""
        from mem_mcp.mcp.tools.get import MemoryGetInput

        with pytest.raises(ValueError, match="must provide either id"):
            MemoryGetInput(
                team_id=uuid4(),
                resource_type="decision",
                # slug missing
            )

    async def test_slug_length_validation(self, pg_pool: Pool) -> None:
        """memory_get rejects slug if it exceeds 64 chars."""
        from mem_mcp.mcp.tools.get import MemoryGetInput

        with pytest.raises(ValueError, match="slug must be 1-64 chars"):
            MemoryGetInput(
                team_id=uuid4(),
                resource_type="decision",
                slug="a" * 65,  # too long
            )
