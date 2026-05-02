"""Content normalization for hashing and dedupe.

Per spec §10.2:
  hash input = NFKC-normalize → strip → lower → collapse whitespace → SHA-256
  embedding input = original content (NOT normalized) — preserves casing/punct
                    for semantic embedding quality
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_hash(text: str) -> str:
    """Canonicalize text for content_hash deduplication.

    Steps (per spec §10.2):
      1. Unicode NFKC normalize (compatibility composition)
      2. Lowercase
      3. Strip leading/trailing whitespace
      4. Collapse internal whitespace runs to single space
    """
    if not isinstance(text, str):
        raise TypeError(f"normalize_for_hash expects str, got {type(text).__name__}")
    t = unicodedata.normalize("NFKC", text)
    t = t.strip().lower()
    t = _WHITESPACE_RE.sub(" ", t)
    return t


def hash_content(text: str) -> str:
    """Return the SHA-256 hex digest of normalize_for_hash(text)."""
    return hashlib.sha256(normalize_for_hash(text).encode("utf-8")).hexdigest()
