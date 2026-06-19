"""Mind-map — collaborative thinking maps layered over core memories.

A map is a root memory plus the nodes that hang off it. The map's identity is
the root memory's slug (resource_type 'map') — the memsys "key". Node content
lives in `memories`; edges in `memory_references`; the lifecycle state
(live/archived, reviewer write-counter, seed origin, graduated index) lives in
the `memory_maps` / `memory_map_membership` / `memory_map_events` tables.

See docs/mindmap-v1-build-design.md for the full design.
"""

from __future__ import annotations

from typing import Final

# Owner decision (2026-06-19): reviewer fires every 15 owned writes (10 is too
# eager). Stored per-map in memory_maps.review_threshold; this is the default
# the open tool seeds new maps with.
DEFAULT_REVIEW_THRESHOLD: Final[int] = 15

# Atomicity / split-not-trim (spec 5f439cd0): past this soft char count a node
# "looks like more than one decision". Advisory only — the node is still
# written; the response carries split_suggested=True. Never truncates.
SOFT_NODE_CHARS: Final[int] = 1200

# node_role values a map node may carry. The root is 'root' (type question);
# working thinking nodes are position/challenge/note. In-map "decisions" are
# represented as ratified positions and only become real KB decisions at
# graduation (close), so 'decision' is NOT a write_node role.
NODE_ROLES: Final[frozenset[str]] = frozenset({"position", "challenge", "note"})

# Ratification strength gradient (spec 526ca485), strongest → weakest.
RATIFICATION_STRENGTHS: Final[tuple[str, ...]] = (
    "survived-challenge",
    "explicit-endorse",
    "delegate",
    "tacit",
)
