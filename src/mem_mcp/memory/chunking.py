"""Deterministic content chunking for chunk-level vector retrieval.

No LLM, no heuristics that vary between runs — pure structural splitting
(per the no-intelligence-in-the-substrate rule):

1. Split on blank lines (paragraphs; markdown headings start new paragraphs
   naturally since they're separated by blank lines in practice).
2. Greedily pack consecutive paragraphs into chunks of ~CHUNK_TARGET_CHARS,
   never exceeding CHUNK_MAX_CHARS.
3. A single paragraph longer than CHUNK_MAX_CHARS is split at sentence
   boundaries; a single sentence longer than CHUNK_MAX_CHARS is hard-wrapped
   (last resort — never silently dropped).

Chunks preserve document order; chunk_index is the escalation handle for
"fetch the surrounding chunks". Capped at MAX_CHUNKS_PER_MEMORY — content
beyond the cap is not chunk-indexed (whole-memory search still covers it
via the tsvector leg).
"""

from __future__ import annotations

import re
from typing import Final

# ~300 tokens per chunk for typical English — small enough for sharp
# retrieval, large enough to carry a rule together with its exception.
CHUNK_TARGET_CHARS: Final[int] = 1_200
CHUNK_MAX_CHARS: Final[int] = 2_000
MAX_CHUNKS_PER_MEMORY: Final[int] = 128

_PARAGRAPH_RE = re.compile(r"\n\s*\n")
# Sentence boundary: terminal punctuation followed by whitespace. Crude but
# deterministic; abbreviation false-splits cost a little chunk balance, not
# correctness.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _split_oversize_paragraph(paragraph: str) -> list[str]:
    """Split one paragraph > CHUNK_MAX_CHARS at sentence boundaries,
    hard-wrapping any single sentence that still exceeds the max."""
    pieces: list[str] = []
    for sentence in _SENTENCE_RE.split(paragraph):
        while len(sentence) > CHUNK_MAX_CHARS:
            pieces.append(sentence[:CHUNK_MAX_CHARS])
            sentence = sentence[CHUNK_MAX_CHARS:]
        if sentence:
            pieces.append(sentence)
    return pieces


def split_into_chunks(content: str) -> list[str]:
    """Split memory content into ordered chunks for embedding.

    Deterministic: same content always yields the same chunks. Whitespace-only
    content yields []. Short content yields a single chunk equal to the
    stripped content.
    """
    text = content.strip()
    if not text:
        return []
    if len(text) <= CHUNK_MAX_CHARS:
        return [text]

    # Flatten paragraphs, pre-splitting any oversize ones.
    units: list[str] = []
    for paragraph in _PARAGRAPH_RE.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > CHUNK_MAX_CHARS:
            units.extend(_split_oversize_paragraph(paragraph))
        else:
            units.append(paragraph)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for unit in units:
        # +2 for the paragraph separator re-joined below
        if current and current_len + len(unit) + 2 > CHUNK_TARGET_CHARS:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0
        current.append(unit)
        current_len += len(unit) + 2
    if current:
        chunks.append("\n\n".join(current))

    return chunks[:MAX_CHUNKS_PER_MEMORY]
