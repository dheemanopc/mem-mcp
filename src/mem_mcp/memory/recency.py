"""Recency-decay lambda lookup per memory type (spec §10.4)."""

from __future__ import annotations

from typing import Final

# spec §10.4: decision/fact decay slowly; notes/snippets decay quickly
RECENCY_LAMBDA_BY_TYPE: Final[dict[str, float]] = {
    "decision": 0.0019,
    "fact": 0.0019,
    "note": 0.05,
    "snippet": 0.10,
    "question": 0.05,
}

# Default lambda used when type is unknown or not provided
DEFAULT_RECENCY_LAMBDA: Final[float] = 0.05


def recency_lambda_for(type_: str | None) -> float:
    """Return the recency-decay lambda for the given memory type.

    Unknown types fall back to DEFAULT_RECENCY_LAMBDA (note-equivalent).
    """
    if type_ is None:
        return DEFAULT_RECENCY_LAMBDA
    return RECENCY_LAMBDA_BY_TYPE.get(type_, DEFAULT_RECENCY_LAMBDA)
