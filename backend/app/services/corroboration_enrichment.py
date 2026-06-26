"""Async, shortlist-gated cross-source corroboration enrichment.

The SLOW corroboration axes — graph collective corroboration (Neo4j queries) and
the GitHub URL fetch — must NOT run on every score. They run here, off the
scoring hot path, ONLY for a candidate who is BOTH a plausible match
(``cv_match_score >= CORROBORATION_ENRICH_MIN_SCORE``) AND already carries a
deterministic flag (triangulation verdict ``review`` / ``strong_review``). That
is the "resolve a real question, on a real candidate" placement — confirm/deny a
flag, never screen everyone. Volume is low by construction (high-match ×
already-flagged), so the load is a small fraction of running it funnel-wide.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication

logger = logging.getLogger("taali.corroboration_enrichment")

_FLAGGED = {"review", "strong_review"}


def should_enrich(application: CandidateApplication) -> bool:
    """Gate: enrich only when a paid axis is enabled AND the candidate is a
    plausible match that already carries a flag worth resolving. Cheap pure read
    — safe to call on the scoring path before dispatching, and re-checked on the
    worker."""
    from ..platform.config import settings

    if not (
        settings.GRAPH_CORROBORATION_ENABLED
        or settings.GITHUB_CORROBORATION_ENABLED
        or settings.GRAPH_OUTCOME_PRIOR_ENABLED
    ):
        return False
    score = getattr(application, "cv_match_score", None)
    if score is None or float(score) < float(settings.CORROBORATION_ENRICH_MIN_SCORE):
        return False
    details = getattr(application, "cv_match_details", None)
    if not isinstance(details, dict):
        return False
    tri = (details.get("integrity_signals") or {}).get("triangulation") or {}
    return tri.get("verdict") in _FLAGGED


def enrich_corroboration(application: CandidateApplication, db: Session) -> dict[str, Any] | None:
    """Compute the gated graph + GitHub corroboration for one application,
    merge into ``cv_match_details.integrity_signals``, re-triangulate, persist.
    Returns the updated triangulation, or ``None`` when nothing changed / both
    axes disabled / no graph / no GitHub URL. Fail-open — never raises."""
    try:
        from ..platform.config import settings
        from .external_corroboration import corroborate_github
        from .fraud_detection import aggregate_triangulation, build_integrity_warnings
        from .graph_corroboration import corroborate_candidate_stack

        details = getattr(application, "cv_match_details", None)
        if not isinstance(details, dict):
            return None
        sig = dict(details.get("integrity_signals") or {})

        cand = getattr(application, "candidate", None)
        cv_sections = (
            getattr(application, "cv_sections", None)
            or (getattr(cand, "cv_sections", None) if cand is not None else None)
            or {}
        )

        changed = False
        graph = corroborate_candidate_stack(
            organization_id=getattr(application, "organization_id", None),
            cv_sections=cv_sections,
            min_observations=settings.GRAPH_CORROBORATION_MIN_OBSERVATIONS,
        )
        if graph is not None:
            sig["graph_corroboration"] = graph
            changed = True
        social = getattr(cand, "social_profiles", None) if cand is not None else None
        github = corroborate_github(cv_sections=cv_sections, social_profiles=social)
        if github is not None:
            sig["github"] = github
            changed = True
        # P4 SHADOW: graph outcome prior → would-be Match nudge, persisted for
        # review only (applied: False). Never touches the score here.
        if settings.GRAPH_OUTCOME_PRIOR_ENABLED:
            from .graph_outcome_prior import build_outcome_prior_shadow, fetch_outcome_prior

            prior = build_outcome_prior_shadow(
                fetch_outcome_prior(application, db),
                max_nudge=settings.GRAPH_OUTCOME_PRIOR_MAX_NUDGE,
            )
            if prior is not None:
                sig["graph_outcome_prior"] = prior
                changed = True
        if not changed:
            return None

        sig["triangulation"] = aggregate_triangulation(sig)
        sig["warnings"] = build_integrity_warnings(sig)
        # Reassign the whole JSON blob so SQLAlchemy detects the mutation.
        new_details = dict(details)
        new_details["integrity_signals"] = sig
        application.cv_match_details = new_details
        db.add(application)
        db.commit()
        return sig["triangulation"]
    except Exception:  # pragma: no cover — never raise out of enrichment
        logger.warning(
            "corroboration enrichment failed for app=%s",
            getattr(application, "id", None), exc_info=True,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return None
