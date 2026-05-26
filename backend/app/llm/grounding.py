"""Evidence-grounding primitive: fuzzy quote location.

``fuzzy_locate(quote, text)`` finds where a model-emitted quote lives in
a source text and returns its character range, or ``None`` when the
quote isn't substantially present. The fast paths catch the common case
(exact / case-insensitive substring); the slow path is a sliding-window
``SequenceMatcher`` similarity check that tolerates LLM paraphrasing
(off-by-a-word, an extra space, a hyphen) while still rejecting quotes
the model fabricated.

This is the primitive cv_matching's ``validate_evidence_grounding`` uses
to verify ``evidence_quotes`` against the CV. It lives in the gateway
layer so any pipeline that emits "claim + source quote" can verify the
quote actually appears in the source — pre-screen reasoning, agent
explanations, or chat answers — without each one reimplementing the
fuzzy matcher.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional


# Fuzzy-quote-match threshold. The LLM frequently paraphrases when
# generating evidence quotes: a phrase that's nearly verbatim in the
# source (off by a word, an extra space, a hyphen) gets emitted as a
# quote that doesn't strictly substring-match. Strict matching dropped
# legitimate evidence; fuzzy matching with a high similarity threshold
# preserves the grounding intent (the quote must substantially appear
# in the source) without punishing close paraphrases.
FUZZY_THRESHOLD = 0.85
# Window scan: for each LLM quote, slide a source window of similar
# length and check the best similarity. ``FUZZY_WINDOW_PAD`` is how
# much extra source context to consider on either side of the
# quote-length window.
FUZZY_WINDOW_PAD = 20
# Don't fuzzy-match tiny quotes — too easy to false-positive. Exact /
# case-insensitive matches on short quotes still go through.
MIN_FUZZY_LEN = 8


def _normalise(s: str) -> str:
    """Collapse whitespace and lowercase for similarity comparison."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def fuzzy_locate(quote: str, text: str) -> Optional[tuple[int, int]]:
    """Find the best fuzzy match for ``quote`` in ``text``.

    Returns ``(start, end)`` of the best matching source span if
    similarity >= ``FUZZY_THRESHOLD``, else ``None``. Exact substring
    hits are fast-pathed; only quotes that miss the exact path pay the
    O(len(text)) sliding-window cost.

    Three tiers, fastest to slowest:
    1. Exact substring match.
    2. Case-insensitive substring match.
    3. Sliding-window ``SequenceMatcher`` ratio at >= ``FUZZY_THRESHOLD``,
       gated by ``MIN_FUZZY_LEN`` to avoid false positives on tiny quotes.
    """
    if not quote or not text:
        return None
    # Fast path: exact substring (most quotes hit this).
    idx = text.find(quote)
    if idx >= 0:
        return (idx, idx + len(quote))

    # Slow path: case-insensitive whitespace-normalised substring.
    text_lower = text.lower()
    quote_lower = quote.lower()
    idx = text_lower.find(quote_lower)
    if idx >= 0:
        return (idx, idx + len(quote))

    # Slowest path: sliding-window fuzzy match.
    quote_norm = _normalise(quote)
    if not quote_norm or len(quote_norm) < MIN_FUZZY_LEN:
        return None

    text_norm = _normalise(text)
    matcher = SequenceMatcher(None, text_norm, quote_norm, autojunk=False)
    blocks = matcher.get_matching_blocks()
    if not blocks:
        return None
    # The longest matching block tells us where in the source the quote
    # most likely came from. Build a window around it and score
    # similarity to decide whether to accept.
    longest = max(blocks, key=lambda b: b.size)
    if longest.size == 0:
        return None
    win_start = max(0, longest.a - FUZZY_WINDOW_PAD)
    win_end = min(len(text_norm), longest.a + len(quote_norm) + FUZZY_WINDOW_PAD)
    window = text_norm[win_start:win_end]
    sim = SequenceMatcher(None, window, quote_norm, autojunk=False).ratio()
    if sim < FUZZY_THRESHOLD:
        return None
    # Re-locate the matched window in the original (unnormalised) text.
    # The normalisation collapsed whitespace, so character offsets shift —
    # we approximate by finding the first matched word from the block.
    pivot_words = quote.split()[:3]
    if pivot_words:
        pivot = " ".join(pivot_words)
        pivot_idx = text.lower().find(pivot.lower())
        if pivot_idx >= 0:
            return (pivot_idx, pivot_idx + len(pivot))
    return (0, min(len(text), len(quote)))


__all__ = ["fuzzy_locate", "FUZZY_THRESHOLD", "FUZZY_WINDOW_PAD", "MIN_FUZZY_LEN"]
