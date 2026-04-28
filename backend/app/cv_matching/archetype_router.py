"""Archetype routing for the v4.2 pipeline (RALPH 2.7).

Picks the best-fitting ``ArchetypeRubric`` for an incoming JD by
cosine-matching against each archetype's ``jd_centroid_text``
embedding. Returns ``None`` when no archetype clears the threshold,
in which case the v4.2 caller falls back to the generic v4.1 prompt
shape (without archetype context).

Centroids are computed once per process and cached. The first call
embeds every archetype's ``jd_centroid_text`` (cheap on the mock
provider; one Voyage call per archetype on production).

Tests inject a fake embedder via ``pick_archetype(..., embed_fn=...)``
to keep them deterministic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Sequence

from .embeddings import cosine_similarity, embed_jd
from .rubrics import ArchetypeRubric, list_rubrics

logger = logging.getLogger("taali.cv_match.archetype_router")

# Cosine floor for "this archetype matches well enough to specialise the
# prompt". Below this, fall back to the generic v4.1 prompt.
DEFAULT_THRESHOLD = 0.55


@dataclass
class _CachedCentroid:
    rubric: ArchetypeRubric
    centroid: list[float]


_centroid_cache: list[_CachedCentroid] | None = None


def _build_centroids(
    embed_fn: Callable[[str, list], list[float]] | None = None,
) -> list[_CachedCentroid]:
    """Embed every archetype's jd_centroid_text. Cached after first call."""
    global _centroid_cache
    if _centroid_cache is not None:
        return _centroid_cache
    embed = embed_fn or (lambda text, reqs: embed_jd(text, reqs))
    cache: list[_CachedCentroid] = []
    for rubric in list_rubrics():
        try:
            centroid = embed(rubric.jd_centroid_text, [])
        except Exception as exc:
            logger.warning(
                "Failed to embed centroid for archetype %s: %s",
                rubric.archetype_id,
                exc,
            )
            continue
        cache.append(_CachedCentroid(rubric=rubric, centroid=centroid))
    _centroid_cache = cache
    return cache


def reset_cache() -> None:
    """Drop the centroid cache. Tests use this to isolate runs."""
    global _centroid_cache
    _centroid_cache = None


@dataclass
class ArchetypeMatch:
    rubric: ArchetypeRubric
    similarity: float


def pick_archetype(
    jd_text: str,
    requirements: Sequence | None = None,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    embed_fn: Callable[[str, list], list[float]] | None = None,
) -> ArchetypeMatch | None:
    """Return the best-matching archetype above ``threshold``, or None.

    ``embed_fn`` is the JD embedder. Defaults to ``embeddings.embed_jd``.
    Tests pass a deterministic stub.
    """
    centroids = _build_centroids(embed_fn=embed_fn)
    if not centroids:
        return None

    embed = embed_fn or (lambda text, reqs: embed_jd(text, reqs))
    jd_vec = embed(jd_text, list(requirements or []))

    best: ArchetypeMatch | None = None
    for entry in centroids:
        sim = cosine_similarity(jd_vec, entry.centroid)
        if best is None or sim > best.similarity:
            best = ArchetypeMatch(rubric=entry.rubric, similarity=sim)

    if best is None or best.similarity < threshold:
        return None

    logger.info(
        "Routed JD to archetype=%s (cosine=%.4f, threshold=%.4f)",
        best.rubric.archetype_id,
        best.similarity,
        threshold,
    )
    return best


__all__ = [
    "ArchetypeMatch",
    "DEFAULT_THRESHOLD",
    "pick_archetype",
    "reset_cache",
]
