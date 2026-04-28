"""Embedding pre-filter for batch CV matching (Phase 2).

When a single JD is being matched against many CVs (typical at the
"new role posted" moment), most candidates are obvious mismatches
and should not consume Haiku tokens. This module embeds every CV
once, cosine-ranks them against the JD embedding, and drops the
bottom half (or anything below a configurable cosine floor).

Critical safety rules — both encoded as tests:

1. Never drop a candidate whose ``application_id`` appears in the
   ``cv_match_overrides`` table. Recruiters have manually validated
   those candidates; embedding similarity is too coarse a signal to
   override their judgment.
2. Never silently drop candidates without logging the cosine score and
   the reason. ``PrefilterDecision`` rows are surfaced via telemetry
   so an operator can audit "who got filtered out and why".

The pre-filter only activates above
``settings.CV_MATCH_V4_PREFILTER_MIN_BATCH``. At small N the
embedding round-trip cost exceeds what the filter saves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Sequence

from .embeddings import cosine_similarity, embed_cv, embed_jd

logger = logging.getLogger("taali.cv_match.prefilter")


@dataclass
class PrefilterCandidate:
    """One CV in a batch.

    ``application_id`` is optional — set to None for ad-hoc CV uploads
    that don't yet have a row in ``candidate_applications``. When None,
    the override safety rule does not apply (there's nothing to look up).
    """

    cv_text: str
    application_id: int | None = None
    candidate_label: str = ""  # human-readable id for logs


@dataclass
class PrefilterDecision:
    candidate: PrefilterCandidate
    cosine_score: float
    kept: bool
    reason: str  # see _REASONS below


_REASONS = (
    "kept_above_threshold",
    "kept_top_half",
    "kept_override_protected",
    "dropped_below_threshold",
    "dropped_bottom_half",
    "skipped_small_batch",
)


def _resolve_settings() -> tuple[bool, float, int, float]:
    """Return (enabled, cosine_threshold, min_batch, top_fraction).

    ``top_fraction`` is hardcoded at 0.5 per the RALPH spec; surfaced as
    a tuple element so tests can monkeypatch the resolver instead of
    individual settings.
    """
    try:
        from ..platform.config import settings

        enabled = bool(getattr(settings, "USE_CV_MATCH_V4_PREFILTER", False))
        threshold = float(
            getattr(settings, "CV_MATCH_V4_PREFILTER_COSINE_THRESHOLD", 0.50) or 0.50
        )
        min_batch = int(
            getattr(settings, "CV_MATCH_V4_PREFILTER_MIN_BATCH", 30) or 30
        )
    except Exception:
        enabled, threshold, min_batch = False, 0.50, 30
    return enabled, threshold, min_batch, 0.5


def _override_protected_application_ids(
    application_ids: Iterable[int],
) -> set[int]:
    """Return the subset of ``application_ids`` that have at least one
    entry in ``cv_match_overrides``. Empty set on DB unavailable.
    """
    ids = [int(i) for i in application_ids if i is not None]
    if not ids:
        return set()
    try:
        from ..models.cv_match_override import CvMatchOverride
        from ..platform.database import SessionLocal
    except Exception as exc:
        logger.debug("Override-protection lookup skipped: %s", exc)
        return set()

    session = SessionLocal()
    try:
        rows = (
            session.query(CvMatchOverride.application_id)
            .filter(CvMatchOverride.application_id.in_(ids))
            .distinct()
            .all()
        )
        return {int(r[0]) for r in rows if r[0] is not None}
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Override-protection lookup failed: %s", exc)
        return set()
    finally:
        session.close()


def prefilter(
    candidates: Sequence[PrefilterCandidate],
    jd_text: str,
    requirements: list | None = None,
    *,
    enabled: bool | None = None,
    cosine_threshold: float | None = None,
    min_batch: int | None = None,
    top_fraction: float = 0.5,
    override_lookup=None,
) -> list[PrefilterDecision]:
    """Run the pre-filter against a candidate list.

    Returns one ``PrefilterDecision`` per candidate in *input order* —
    callers iterate the list and ignore decisions where ``kept=False``.

    Args:
        candidates: candidates to filter.
        jd_text: job specification.
        requirements: optional recruiter requirements (threaded into the
            JD embedding so the pre-filter respects what the recruiter
            actually cares about).
        enabled / cosine_threshold / min_batch: optional overrides for
            the corresponding settings (used by tests). When None we
            read from ``settings``.
        top_fraction: keep the top N% by cosine after applying the
            threshold floor. Default 0.5 (drop bottom half), per spec.
        override_lookup: callable for override-protection. Defaults to
            ``_override_protected_application_ids``. Tests inject a stub.
    """
    cfg_enabled, cfg_threshold, cfg_min, _ = _resolve_settings()
    enabled = cfg_enabled if enabled is None else enabled
    threshold = cfg_threshold if cosine_threshold is None else cosine_threshold
    min_n = cfg_min if min_batch is None else min_batch

    candidates = list(candidates)
    if not candidates:
        return []

    if not enabled or len(candidates) < min_n:
        # Below-threshold batch: do nothing (every candidate kept).
        return [
            PrefilterDecision(
                candidate=c,
                cosine_score=0.0,
                kept=True,
                reason="skipped_small_batch",
            )
            for c in candidates
        ]

    # 1. Embed the JD once.
    jd_vec = embed_jd(jd_text, requirements)

    # 2. Score every candidate. Embedding is cached behind the scenes.
    scored: list[tuple[PrefilterCandidate, float]] = []
    for c in candidates:
        vec = embed_cv(c.cv_text)
        sim = cosine_similarity(jd_vec, vec)
        scored.append((c, sim))

    # 3. Lookup override protection. Only candidates with an
    #    application_id can be protected.
    lookup = override_lookup or _override_protected_application_ids
    protected_ids = lookup(
        c.application_id for c, _ in scored if c.application_id is not None
    )

    # 4. Decide each candidate. Order:
    #    a) override-protected → always kept
    #    b) below threshold → dropped (unless protected)
    #    c) above threshold → keep top fraction by cosine, drop the rest

    above_threshold: list[tuple[PrefilterCandidate, float]] = []
    decisions_by_id: dict[int, PrefilterDecision] = {}
    decisions: list[PrefilterDecision] = []
    for c, sim in scored:
        if c.application_id is not None and c.application_id in protected_ids:
            d = PrefilterDecision(
                candidate=c,
                cosine_score=sim,
                kept=True,
                reason="kept_override_protected",
            )
            decisions.append(d)
            continue
        if sim < threshold:
            d = PrefilterDecision(
                candidate=c,
                cosine_score=sim,
                kept=False,
                reason="dropped_below_threshold",
            )
            decisions.append(d)
            continue
        above_threshold.append((c, sim))

    # 5. Of the above-threshold candidates, keep the top ``top_fraction``
    #    by cosine. Ties broken by appearance order (stable sort).
    above_threshold_sorted = sorted(
        above_threshold, key=lambda t: t[1], reverse=True
    )
    keep_count = max(1, int(len(above_threshold_sorted) * top_fraction + 0.5))
    keep_set = {id(c) for c, _ in above_threshold_sorted[:keep_count]}
    for c, sim in above_threshold:
        kept = id(c) in keep_set
        decisions.append(
            PrefilterDecision(
                candidate=c,
                cosine_score=sim,
                kept=kept,
                reason="kept_top_half" if kept else "dropped_bottom_half",
            )
        )

    # Re-sort decisions back into input order.
    cand_index = {id(c): i for i, c in enumerate(candidates)}
    decisions.sort(key=lambda d: cand_index[id(d.candidate)])

    # 6. Audit log every dropped candidate.
    for d in decisions:
        if not d.kept:
            logger.info(
                "Prefilter dropped candidate label=%r app_id=%s cosine=%.4f reason=%s",
                d.candidate.candidate_label or "?",
                d.candidate.application_id,
                d.cosine_score,
                d.reason,
            )

    return decisions


__all__ = [
    "PrefilterCandidate",
    "PrefilterDecision",
    "prefilter",
]
