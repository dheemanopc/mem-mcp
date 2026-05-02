"""Versioning helpers for decision/fact memory types.

Full implementation lands with memory.update (T-7.2). For now this module
exists so other modules can import the constants/types they'll need.
"""

from __future__ import annotations

from typing import Final

VERSIONED_TYPES: Final[frozenset[str]] = frozenset({"decision", "fact"})
NON_VERSIONED_TYPES: Final[frozenset[str]] = frozenset({"note", "snippet", "question"})
