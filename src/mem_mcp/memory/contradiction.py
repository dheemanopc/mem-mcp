"""Contradiction detection on memory_write — NLI via Ollama (issue #315).

Cosine similarity measures semantic proximity, not factual opposition —
"we use PostgreSQL" and "PostgreSQL supports JSONB" score high against a
new Postgres memory without conflicting. Natural Language Inference (NLI)
classifies a pair of statements as CONTRADICTION / NEUTRAL / ENTAILMENT,
which is the signal we actually want. A small generative model on the
existing Ollama instance serves it with a structured prompt.

Flow (advisory, never blocks the write):
  1. Candidate fetch: N most recent non-deleted same-type memories
     (recency window — no cosine, no pgvector).
  2. NLI classification per candidate via Ollama /api/generate.
  3. Filter to contradiction_score >= threshold, sort desc, cap at limit.

On any Ollama failure (connect error, timeout, malformed payload) the
check logs a warning and returns [] — the write always proceeds.

``ContradictionChecker`` is the Protocol boundary injected via ToolDeps
(GUIDELINES §1.2); ``NoopContradictionChecker`` is the default when
``nli_backend = "none"`` (backwards-compatible no-op), ``OllamaNliChecker``
is wired when ``nli_backend = "ollama"``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from mem_mcp.logging_setup import get_logger

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

_log = get_logger("mem_mcp.memory.contradiction")

NLI_DEFAULT_THRESHOLD = 0.7
NLI_DEFAULT_LIMIT = 3
NLI_DEFAULT_CANDIDATE_WINDOW = 20
NLI_DEFAULT_MODEL = "llama3.2:1b"

# First 200 chars of candidate content returned to the caller.
SNIPPET_MAX_CHARS = 200

_NLI_PROMPT_TEMPLATE = """Memory A: {new_content}
Memory B: {candidate_content}

Do Memory A and Memory B state contradictory facts? Consider whether they make conflicting claims about the same topic.

Reply with exactly one of:
CONTRADICTION    (conflicting facts, score = your confidence 0.0-1.0)
NEUTRAL          (compatible or unrelated)
ENTAILMENT       (one supports the other)

Example: CONTRADICTION 0.85"""

# "CONTRADICTION 0.85" — label, then optional confidence anywhere after it.
_NLI_RESPONSE_RE = re.compile(
    r"\b(CONTRADICTION|NEUTRAL|ENTAILMENT)\b(?:[^\d]*([01](?:\.\d+)?))?",
    re.IGNORECASE,
)

_CANDIDATE_SQL = """
SELECT id, content, type, tags, created_at
FROM memories
WHERE tenant_id = $1 AND type = $2
  AND deleted_at IS NULL AND is_current = true
ORDER BY created_at DESC
LIMIT $3
"""


@dataclass(frozen=True)
class ContradictionCandidate:
    memory_id: UUID
    content_snippet: str  # first 200 chars
    contradiction_score: float  # 0.0-1.0, NLI confidence
    type: str
    tags: list[str]
    created_at: datetime


def parse_nli_response(text: Any) -> tuple[str, float]:
    """Parse an Ollama NLI reply into (label, confidence).

    Accepts "CONTRADICTION 0.85" with arbitrary surrounding chatter.
    Malformed or missing label → ("NEUTRAL", 0.0) so noise never
    produces a false contradiction. Missing score defaults to 0.0.
    """
    if not isinstance(text, str):
        return ("NEUTRAL", 0.0)
    m = _NLI_RESPONSE_RE.search(text)
    if m is None:
        return ("NEUTRAL", 0.0)
    label = m.group(1).upper()
    score = 0.0
    if m.group(2) is not None:
        try:
            score = min(1.0, max(0.0, float(m.group(2))))
        except ValueError:
            score = 0.0
    return (label, score)


async def _ollama_nli_classify(
    *,
    url: str,
    model: str,
    new_content: str,
    candidate_content: str,
    timeout_seconds: float,
) -> tuple[str, float]:
    """Single NLI classification round-trip; raises httpx errors to the caller."""
    # Local import keeps unit tests from paying httpx import cost at module load.
    import httpx

    prompt = _NLI_PROMPT_TEMPLATE.format(
        new_content=new_content,
        candidate_content=candidate_content,
    )
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(
            f"{url.rstrip('/')}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0},
            },
        )
        resp.raise_for_status()
        data: Any = resp.json()
    if not isinstance(data, dict):
        return ("NEUTRAL", 0.0)
    return parse_nli_response(data.get("response"))


async def find_contradiction_candidates(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    new_content: str,
    type_: str,
    *,
    ollama_url: str,
    model: str = NLI_DEFAULT_MODEL,
    threshold: float = NLI_DEFAULT_THRESHOLD,
    limit: int = NLI_DEFAULT_LIMIT,
    window: int = NLI_DEFAULT_CANDIDATE_WINDOW,
    timeout_seconds: float = 30.0,
) -> list[ContradictionCandidate]:
    """Fetch recent same-type memories, score each via Ollama NLI, return
    those with contradiction_score >= threshold (sorted desc, capped at limit).

    On Ollama failure, logs a warning and returns [] — never raises.
    """
    rows = await conn.fetch(_CANDIDATE_SQL, tenant_id, type_, window)
    if not rows:
        return []

    hits: list[ContradictionCandidate] = []
    try:
        for row in rows:
            label, score = await _ollama_nli_classify(
                url=ollama_url,
                model=model,
                new_content=new_content,
                candidate_content=row["content"],
                timeout_seconds=timeout_seconds,
            )
            if label == "CONTRADICTION" and score >= threshold:
                hits.append(
                    ContradictionCandidate(
                        memory_id=row["id"],
                        content_snippet=row["content"][:SNIPPET_MAX_CHARS],
                        contradiction_score=score,
                        type=row["type"],
                        tags=list(row["tags"] or []),
                        created_at=row["created_at"],
                    )
                )
    except Exception as exc:
        _log.warning(
            "ollama NLI contradiction check failed; returning no candidates",
            extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
        )
        return []

    hits.sort(key=lambda c: c.contradiction_score, reverse=True)
    return hits[:limit]


class ContradictionChecker(Protocol):
    """Boundary for the memory_write contradiction check (GUIDELINES §1.2)."""

    async def find_candidates(
        self,
        conn: asyncpg.Connection,
        tenant_id: UUID,
        new_content: str,
        type_: str,
        *,
        limit: int,
    ) -> list[ContradictionCandidate]: ...


class NoopContradictionChecker:
    """Always returns []. Wired when nli_backend='none' (the default) so
    check_contradictions=true stays a backwards-compatible no-op."""

    async def find_candidates(
        self,
        conn: asyncpg.Connection,
        tenant_id: UUID,
        new_content: str,
        type_: str,
        *,
        limit: int,
    ) -> list[ContradictionCandidate]:
        return []


class OllamaNliChecker:
    """Production checker — delegates to find_contradiction_candidates."""

    def __init__(
        self,
        *,
        url: str,
        model: str = NLI_DEFAULT_MODEL,
        threshold: float = NLI_DEFAULT_THRESHOLD,
        window: int = NLI_DEFAULT_CANDIDATE_WINDOW,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.url = url
        self.model = model
        self.threshold = threshold
        self.window = window
        self.timeout_seconds = timeout_seconds

    async def find_candidates(
        self,
        conn: asyncpg.Connection,
        tenant_id: UUID,
        new_content: str,
        type_: str,
        *,
        limit: int,
    ) -> list[ContradictionCandidate]:
        return await find_contradiction_candidates(
            conn,
            tenant_id,
            new_content,
            type_,
            ollama_url=self.url,
            model=self.model,
            threshold=self.threshold,
            limit=limit,
            window=self.window,
            timeout_seconds=self.timeout_seconds,
        )
