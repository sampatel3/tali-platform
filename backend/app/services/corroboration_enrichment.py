"""Async, shortlist-gated cross-source corroboration enrichment.

The PAID / SLOW corroboration axes — graph collective corroboration (Neo4j
queries) and the LinkedIn URL fetch (a $0.05-0.30 provider call) — must NOT run
on every score. They run here, off the scoring hot path, ONLY for a candidate
who is BOTH a plausible match (``cv_match_score >= CORROBORATION_ENRICH_MIN_SCORE``)
AND already carries a deterministic flag (triangulation verdict ``review`` /
``strong_review``). That is the "pay to resolve a real question, on a real
candidate" placement: spend the LinkedIn dollar to confirm/deny a flag, never
to screen everyone. Volume is low by construction (high-match × already-flagged),
so the effective spend is a small fraction of running it funnel-wide.
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

    if not (settings.GRAPH_CORROBORATION_ENABLED or settings.LINKEDIN_CORROBORATION_ENABLED):
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
    """Compute the gated graph + LinkedIn corroboration for one application,
    merge into ``cv_match_details.integrity_signals``, re-triangulate, persist.
    Returns the updated triangulation, or ``None`` when nothing changed / both
    axes disabled / no graph / no LinkedIn URL. Fail-open — never raises."""
    try:
        from ..platform.config import settings
        from .external_corroboration import corroborate_linkedin
        from .fraud_detection import aggregate_triangulation
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
        linkedin = corroborate_linkedin(cv_sections=cv_sections, social_profiles=social)
        if linkedin is not None:
            sig["linkedin"] = linkedin
            changed = True
        if not changed:
            return None

        sig["triangulation"] = aggregate_triangulation(sig)
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
