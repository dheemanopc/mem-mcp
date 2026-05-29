"""Slug generator + reserved-list + supersession redirect.

Per memsys foundation 1c47bd99 + ratification 6d3b4bfe:
- Amendment #11: 64-char cap chosen for AI-token economics.
- Amendment #12: non-ASCII slugs rejected for now.
- Amendment #13: slug-gen failure surfaces structured error; NO fallback to 'untitled'.
- Amendment #14: PK+retry in-transaction; no separate generator service.
- Amendment #15: reserved-slug v1 seed (admin/api/health/metrics/internal/system/null/undefined/root)
  PLUS dynamic gather of web/handlers route names at module import.

Per amendment #5 from 99eacba4: caller-provided slug_clue (not title-derived).
Auto-derivation is "the store guessing what the author meant" which violates
the no-intelligence principle (28000039). Caller-provided + reject-on-failure
is the store executing a rule.

Title-derivation lives in a SEPARATE function used ONLY for the migration
backfill of existing memories (when 0023's visibility-widen + personal-team
migration ships in a later PR). Documented; not exposed as a public helper.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


# Per amendment #15 — v1 reserved slug list. Owner may amend later.
RESERVED_SLUGS: frozenset[str] = frozenset(
    {
        "admin",
        "api",
        "health",
        "metrics",
        "internal",
        "system",
        "null",
        "undefined",
        "root",
        # Common web/handlers paths that are system routes; gather dynamically below
        # to keep this in sync with src/mem_mcp/web/handlers/.
    }
)


def _dynamic_reserved_route_names() -> frozenset[str]:
    """Gather slug-shaped names of system routes from src/mem_mcp/web/handlers.

    Returns module stem names (e.g. 'memories', 'feedback', 'export') that
    a user could accidentally collide with if they slug something like 'memories'.
    Conservative: also includes any other handler module names that exist now.
    """
    from pathlib import Path

    handlers_dir = Path(__file__).resolve().parent.parent / "web" / "handlers"
    if not handlers_dir.is_dir():
        return frozenset()
    return frozenset(
        f.stem.lower()
        for f in handlers_dir.iterdir()
        if f.is_file() and f.suffix == ".py" and f.stem != "__init__"
    )


# Combined reserved set; computed at import time.
ALL_RESERVED_SLUGS: frozenset[str] = RESERVED_SLUGS | _dynamic_reserved_route_names()


# Per amendment #11 + #12: ASCII lowercase, hyphens, 64 chars max.
SLUG_REGEX = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")
MAX_SLUG_LEN = 64
MAX_SLUG_RETRY = 100  # per amendment #13: exhaustion is a structured error, not a fallback


ResourceType = Literal["decision", "fact"]


class SlugClueRequiredError(Exception):
    """Caller did not provide slug_clue when one was required."""


class SlugClueInvalidError(Exception):
    """slug_clue does not normalize to a valid slug (empty after normalize, or non-ASCII)."""


class SlugClueReservedError(Exception):
    """slug_clue (after normalize) hits a reserved word."""


class SlugGenerationExhaustedError(Exception):
    """All retries exhausted (>100 suffix attempts). Operator alert."""


def normalize_slug_clue(clue: str | None) -> str | None:
    """Normalize an arbitrary user-supplied clue to a candidate slug.

    Per amendment #12: non-ASCII INPUT is rejected (returns None).
    Per amendment #13: empty result → caller raises SlugClueInvalidError.

    Returns:
        Normalized slug, or None if input is non-ASCII OR result is empty.
    """
    if clue is None:
        return None
    # Reject non-ASCII INPUT outright (amendment #12).
    if not clue.isascii():
        return None
    # Lowercase + replace any non-[a-z0-9] with '-' + collapse repeated '-' + trim edges + cap at 64.
    s = re.sub(r"[^a-z0-9-]", "-", clue.lower())
    s = re.sub(r"-+", "-", s).strip("-")
    s = s[:MAX_SLUG_LEN].rstrip("-")
    if not s:
        return None
    # Final regex check (defensive).
    if not SLUG_REGEX.fullmatch(s):
        return None
    return s


async def insert_slug_with_retry(
    conn: asyncpg.Connection,
    *,
    team_id: UUID,
    resource_type: ResourceType,
    clue: str,
    memory_id: UUID,
    title: str,
) -> str:
    """Insert a slug row with PK+retry on collision.

    Steps:
    1. Normalize the clue per normalize_slug_clue.
    2. Reject if reserved.
    3. Try INSERT with the base slug; on unique violation, append -2, -3, ...
       up to MAX_SLUG_RETRY attempts.
    4. Return the actual slug used (may differ from the clue).

    Raises:
        SlugClueRequiredError: if clue is None or empty.
        SlugClueInvalidError: if clue normalizes to empty / non-ASCII / fails regex.
        SlugClueReservedError: if base normalizes to a reserved name.
        SlugGenerationExhaustedError: if MAX_SLUG_RETRY exceeded.
    """
    import asyncpg as ap_module

    if not clue:
        raise SlugClueRequiredError("slug_clue is required for decision/fact memories")

    base = normalize_slug_clue(clue)
    if base is None:
        raise SlugClueInvalidError(
            f"slug_clue {clue!r} normalizes to empty or contains non-ASCII; "
            "use ASCII lowercase letters / digits / hyphens; 1-64 chars"
        )

    if base in ALL_RESERVED_SLUGS:
        raise SlugClueReservedError(f"slug {base!r} is reserved; choose a different name")

    # Try base first, then base-2, base-3, ... up to MAX_SLUG_RETRY.
    for attempt in range(1, MAX_SLUG_RETRY + 1):
        candidate = base if attempt == 1 else f"{base}-{attempt}"
        if len(candidate) > MAX_SLUG_LEN:
            # Truncate base to make room for the -N suffix.
            suffix = f"-{attempt}"
            candidate = base[: MAX_SLUG_LEN - len(suffix)].rstrip("-") + suffix
        # SAVEPOINT per attempt: a UniqueViolationError under a concurrent
        # writer aborts the OUTER txn unless we wrap each INSERT in a nested
        # transaction (asyncpg's `conn.transaction()` issues SAVEPOINT when
        # already inside a txn). Without this, retry within the same outer
        # txn is impossible — the outer txn enters error state on first
        # collision and rejects all subsequent commands.
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO slugs (team_id, resource_type, slug, memory_id, title)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    team_id,
                    resource_type,
                    candidate,
                    memory_id,
                    title,
                )
            return candidate
        except ap_module.exceptions.UniqueViolationError:
            continue
    raise SlugGenerationExhaustedError(
        f"could not find a free slug after {MAX_SLUG_RETRY} attempts; "
        f"base was {base!r} in team {team_id!s}"
    )


async def redirect_slug_on_supersession(
    conn: asyncpg.Connection,
    *,
    old_memory_id: UUID,
    new_memory_id: UUID,
) -> bool:
    """Move the slug pointer from old → new on supersession.

    Returns True if a slug was moved; False if old_memory_id had no slug.

    Use case: when memory A is superseded by A', slug points at A' (current
    canonical version). Old version A reachable by UUID only.
    """
    row = await conn.fetchrow(
        "SELECT team_id, resource_type, slug FROM slugs WHERE memory_id = $1",
        old_memory_id,
    )
    if row is None:
        return False
    await conn.execute(
        """
        UPDATE slugs
           SET memory_id = $1, updated_at = now()
         WHERE team_id = $2 AND resource_type = $3 AND slug = $4
        """,
        new_memory_id,
        row["team_id"],
        row["resource_type"],
        row["slug"],
    )
    return True


async def lookup_slug(
    conn: asyncpg.Connection,
    *,
    team_id: UUID,
    resource_type: ResourceType,
    slug: str,
) -> dict[str, object] | None:
    """Find a memory by slug. Returns {memory_id, title, updated_at} or None."""
    row = await conn.fetchrow(
        """
        SELECT memory_id, title, updated_at
        FROM slugs
        WHERE team_id = $1 AND resource_type = $2 AND slug = $3
        """,
        team_id,
        resource_type,
        slug,
    )
    return dict(row) if row else None
