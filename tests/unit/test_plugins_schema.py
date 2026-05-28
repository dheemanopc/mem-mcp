"""Unit tests for plugin schema name sanitization."""

from __future__ import annotations

import pytest

from mem_mcp.plugins.schema import schema_name_for


class TestSchemaNameFor:
    """Test schema name sanitization for plugin ids."""

    def test_simple_id(self) -> None:
        """Test that simple alphanumeric ids are prefixed."""
        assert schema_name_for("kite") == "plugin_kite"

    def test_kebab_id(self) -> None:
        """Test that kebab-case ids are converted to snake_case."""
        assert schema_name_for("mem-skill-reminders") == "plugin_mem_skill_reminders"

    def test_mixed_case_normalized(self) -> None:
        """Test that uppercase is lowercased."""
        assert schema_name_for("MySkill") == "plugin_myskill"

    def test_kebab_and_upper(self) -> None:
        """Test kebab + uppercase normalized together."""
        assert schema_name_for("My-Cool-Skill") == "plugin_my_cool_skill"

    def test_unsafe_special_chars_raises(self) -> None:
        """Test that special characters (besides -) raise ValueError."""
        with pytest.raises(ValueError, match="cannot be mapped to a safe PG schema name"):
            schema_name_for("skill@danger")

    def test_unsafe_space_raises(self) -> None:
        """Test that spaces raise ValueError."""
        with pytest.raises(ValueError, match="cannot be mapped to a safe PG schema name"):
            schema_name_for("skill with space")
