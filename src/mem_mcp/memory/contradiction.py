"""Contradiction detection via Ollama NLI (Natural Language Inference).

Detection flow:
1. Fetch the N most recent non-deleted memories of the same type (recency window).
2. For each, call Ollama /api/generate with a structured classification prompt.
3. Parse label (CONTRADICTION/NEUTRAL/ENTAILMENT) + confidence score.
4. Return candidates where contradiction_score >= threshold, up to limit.

On any Ollama failure the candidate is skipped and a warning is logged —
the write always proceeds regardless.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

NLI_DEFAULT_THRESHOLD = 0.7
NLI_DEFAULT_LIMIT = 3
NLI_DEFAULT_CANDIDATE_WINDOW = 20
NLI_DEFAULT_MODEL = "llama3.2:1b"

_NLI_PROMPT = """\
Memory A: {a}
Memory B: {b}

Do Memory A and Memory B state contradictory facts? Consider whether they make \
conflicting claims about the same topic.

Reply with exactly one of:
CONTRADICTION <score>   (conflicting facts, score = your confidence 0.0-1.0)
NEUTRAL <score>         (compatible or unrelated)
ENTAILMENT <score>      (one supports the other)

Example: CONTRADICTION 0.85"""

_NLI_RE = re.compile(r"(CONTRADICTION|NEUTRAL|ENTAILMENT)\s+([\d.]+)", re.IGNORECASE)


class ContradictionCandidate(BaseModel):
    memory_id: UUID
    content_snippet: str        # first 200 chars of the candidate memory
    contradiction_score: float  # 0.0-1.0, NLI model confidence
    type: str
    tags: list[str]
    created_at: datetime


def _parse_nli_response(text: str) -> tuple[str, float]:
    """Parse 'CONTRADICTION 0.85' → ('CONTRADICTION', 0.85). Defaults to NEUTRAL 0.0."""
    m = _NLI_RE.search(text.strip())
    if m:
        label = m.group(1).upper()
        score = min(1.0, max(0.0, float(m.group(2))))
        return label, score
    return "NEUTRAL", 0.0


async def _score_contradiction(a: str, b: str, ollama_url: str, model: str) -> float:
    """POST to Ollama /api/generate; return contradiction confidence 0.0-1.0."""
    import httpx  # local import — keeps test import cost low

    prompt = _NLI_PROMPT.format(a=a[:500], b=b[:500])
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{ollama_url.rstrip('/')}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
        )
        resp.raise_for_status()
        data = resp.json()

    raw = data.get("response", "")
    label, score = _parse_nli_response(raw)
    return score if label == "CONTRADICTION" else 0.0


async def find_contradiction_candidates(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    new_content: str,
    type_: str,
    ollama_url: str,
    model: str = NLI_DEFAULT_MODEL,
    threshold: float = NLI_DEFAULT_THRESHOLD,
    limit: int = NLI_DEFAULT_LIMIT,
    window: int = NLI_DEFAULT_CANDIDATE_WINDOW,
) -> list[ContradictionCandidate]:
    """Fetch recent same-type memories, score each via Ollama NLI, return contradictions.

    Returns up to `limit` candidates with contradiction_score >= threshold,
    sorted by score descending. Returns [] on Ollama failure (never raises).
    """
    rows = await conn.fetch(
        """
        SELECT id, content, type, tags, created_at
        FROM memories
        WHERE tenant_id = $1 AND type = $2
          AND deleted_at IS NULL AND is_current = true
        ORDER BY created_at DESC
        LIMIT $3
        """,
        tenant_id,
        type_,
        window,
    )

    results: list[ContradictionCandidate] = []
    for row in rows:
        try:
            score = await _score_contradiction(new_content, row["content"], ollama_url, model)
        except Exception as exc:
            logger.warning("NLI scoring failed for candidate %s: %s", row["id"], exc)
            continue
        if score >= threshold:
            results.append(
                ContradictionCandidate(
                    memory_id=row["id"],
                    content_snippet=row["content"][:200],
                    contradiction_score=score,
                    type=row["type"],
                    tags=list(row["tags"] or []),
                    created_at=row["created_at"],
                )
            )

    results.sort(key=lambda c: c.contradiction_score, reverse=True)
    return results[:limit]
