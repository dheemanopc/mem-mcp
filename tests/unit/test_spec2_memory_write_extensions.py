"""Tests for memory_write extensions (Spec 2 stage B): team_id, visibility, slug_clue, references, fragment_id."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from asyncpg import Pool  # type: ignore[import-untyped]

pytestmark = pytest.mark.asyncio


class TestMemoryWriteSlugClue:
    """Tests for slug_clue parameter."""

    async def test_decision_without_slug_clue_rejects(self, pg_pool: Pool) -> None:
        """Decision type without slug_clue raises validation error."""
        # This is a validator test — no DB needed, just Pydantic validation
        from mem_mcp.mcp.tools.write import MemoryWriteInput

        with pytest.raises(ValueError, match="slug_clue is required"):
            MemoryWriteInput(
                content="test content",
                type="decision",
                slug_clue=None,  # Missing
            )

    async def test_fact_with_slug_clue_accepted(self, pg_pool: Pool) -> None:
        """Fact type with slug_clue passes validation."""
        from mem_mcp.mcp.tools.write import MemoryWriteInput

        inp = MemoryWriteInput(
            content="test content",
            type="fact",
            slug_clue="my-fact",
        )
        assert inp.slug_clue == "my-fact"

    async def test_note_with_slug_clue_rejects(self, pg_pool: Pool) -> None:
        """Note type with slug_clue raises validation error."""
        from mem_mcp.mcp.tools.write import MemoryWriteInput

        with pytest.raises(ValueError, match="slug_clue only valid"):
            MemoryWriteInput(
                content="test content",
                type="note",
                slug_clue="should-fail",
            )


class TestMemoryWriteReferences:
    """Tests for references parameter."""

    async def test_valid_uuid_reference(self, pg_pool: Pool) -> None:
        """Reference with target_uuid passes validation."""
        from mem_mcp.mcp.tools.write import MemoryWriteInput

        target_uuid = uuid4()
        inp = MemoryWriteInput(
            content="test content",
            type="note",
            references=[
                {
                    "target_uuid": str(target_uuid),
                    "reference_kind": "cites",
                }
            ],
        )
        assert len(inp.references) == 1
        assert inp.references[0].target_uuid == target_uuid

    async def test_valid_slug_reference(self, pg_pool: Pool) -> None:
        """Reference with (team_id, resource_type, slug) passes validation."""
        from mem_mcp.mcp.tools.write import MemoryWriteInput

        team_id = uuid4()
        inp = MemoryWriteInput(
            content="test content",
            type="note",
            references=[
                {
                    "target_team_id": str(team_id),
                    "target_resource_type": "decision",
                    "target_slug": "my-decision",
                    "reference_kind": "depends-on",
                }
            ],
        )
        assert len(inp.references) == 1
        assert inp.references[0].target_team_id == team_id

    async def test_reference_with_both_uuid_and_slug_rejects(self, pg_pool: Pool) -> None:
        """Reference providing both UUID and slug rejects."""
        from mem_mcp.mcp.tools.write import MemoryWriteInput

        with pytest.raises(ValueError, match="cannot provide both"):
            MemoryWriteInput(
                content="test content",
                type="note",
                references=[
                    {
                        "target_uuid": str(uuid4()),
                        "target_team_id": str(uuid4()),
                        "target_resource_type": "decision",
                        "target_slug": "slug",
                        "reference_kind": "cites",
                    }
                ],
            )

    async def test_reference_with_neither_uuid_nor_slug_rejects(self, pg_pool: Pool) -> None:
        """Reference providing neither UUID nor slug rejects."""
        from mem_mcp.mcp.tools.write import MemoryWriteInput

        with pytest.raises(ValueError, match="must provide either target_uuid"):
            MemoryWriteInput(
                content="test content",
                type="note",
                references=[
                    {
                        "reference_kind": "cites",
                    }
                ],
            )


class TestMemoryWriteTeamAndVisibility:
    """Tests for team_id and visibility parameters."""

    async def test_visibility_team_accepted(self, pg_pool: Pool) -> None:
        """visibility='team' is accepted."""
        from mem_mcp.mcp.tools.write import MemoryWriteInput

        inp = MemoryWriteInput(
            content="test content",
            type="note",
            visibility="team",
        )
        assert inp.visibility == "team"

    async def test_visibility_external_accepted(self, pg_pool: Pool) -> None:
        """visibility='external' is accepted."""
        from mem_mcp.mcp.tools.write import MemoryWriteInput

        inp = MemoryWriteInput(
            content="test content",
            type="note",
            visibility="external",
        )
        assert inp.visibility == "external"

    async def test_visibility_public_accepted(self, pg_pool: Pool) -> None:
        """visibility='public' is accepted."""
        from mem_mcp.mcp.tools.write import MemoryWriteInput

        inp = MemoryWriteInput(
            content="test content",
            type="note",
            visibility="public",
        )
        assert inp.visibility == "public"


class TestMemoryWriteFragmentId:
    """Tests for fragment_id parameter."""

    async def test_fragment_id_accepted(self, pg_pool: Pool) -> None:
        """fragment_id is accepted and passed through."""
        from mem_mcp.mcp.tools.write import MemoryWriteInput

        inp = MemoryWriteInput(
            content="test content",
            type="note",
            fragment_id=42,
        )
        assert inp.fragment_id == 42

    async def test_fragment_id_none_accepted(self, pg_pool: Pool) -> None:
        """fragment_id=None is accepted (for non-threaded writes)."""
        from mem_mcp.mcp.tools.write import MemoryWriteInput

        inp = MemoryWriteInput(
            content="test content",
            type="note",
            fragment_id=None,
        )
        assert inp.fragment_id is None
