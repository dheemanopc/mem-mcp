"""Deterministic keyword-phrase extraction for the space-vocabulary eval.

No LLM (substrate rule): phrases are stopword-boundary n-grams — split text
into candidate runs between stopwords/punctuation, keep 2-4 word phrases
(spec: phrases over unigrams; single-word embeddings are promiscuous),
lowercase, frequency-counted.

This is the Phase 0 stand-in for the spec's write-time LLM keyword
extraction. If the viability gate fails specifically on vocabulary quality,
that is the signal the LLM extraction dependency is load-bearing — a
separate decision (no-LLM-in-pipeline rule applies).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Final

# Compact English stopword list — boundary markers, not linguistics.
_STOPWORDS: Final[frozenset[str]] = frozenset(
    """a about above after again all also am an and any are as at be because been
    before being below between both but by can did do does doing down during each
    few for from further had has have having he her here hers him his how i if in
    into is it its itself just me more most my no nor not now of off on once only
    or other our ours out over own per s same she should so some such t than that
    the their theirs them then there these they this those through to too under
    until up very was we were what when where which while who whom why will with
    would you your yours""".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_./:-]*")

MIN_PHRASE_WORDS: Final[int] = 2
MAX_PHRASE_WORDS: Final[int] = 4


def extract_phrases(text: str) -> Counter[str]:
    """Extract 2-4 word keyword phrases with counts from one document.

    Deterministic: candidate runs are maximal sequences of non-stopword
    tokens; each run contributes its full span plus its leading 2..4-word
    sub-spans (so a 6-word run still yields usable phrases).
    """
    counts: Counter[str] = Counter()
    for line in text.lower().splitlines():
        run: list[str] = []
        for token in [*_TOKEN_RE.findall(line), "."]:  # sentinel flushes the tail
            if token in _STOPWORDS or token == ".":
                _flush_run(run, counts)
                run = []
            else:
                run.append(token)
        _flush_run(run, counts)
    return counts


def _flush_run(run: list[str], counts: Counter[str]) -> None:
    n = len(run)
    if n < MIN_PHRASE_WORDS:
        return
    for size in range(MIN_PHRASE_WORDS, MAX_PHRASE_WORDS + 1):
        for i in range(0, n - size + 1):
            counts[" ".join(run[i : i + size])] += 1


def build_vocabulary(documents: list[str], *, max_size: int) -> list[tuple[str, int]]:
    """Aggregate phrase counts across documents → top max_size by frequency.

    Frequency is document-level occurrence (a phrase repeated 50x in one
    memo shouldn't drown the vocabulary). Ties prefer longer (more
    specific) phrases, then alphabetical — deterministic.
    """
    total: Counter[str] = Counter()
    for doc in documents:
        for phrase in extract_phrases(doc):
            total[phrase] += 1
    ranked = sorted(total.items(), key=lambda kv: (-kv[1], -len(kv[0].split()), kv[0]))
    return ranked[:max_size]
