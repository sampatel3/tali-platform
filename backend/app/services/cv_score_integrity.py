"""Deterministic integrity-signal enrichment for CV scoring."""

from __future__ import annotations

import logging

from ..models.candidate_application import CandidateApplication

logger = logging.getLogger("taali.cv_score_orchestrator")


def _augment_integrity_signals(
    existing: dict | None,
    application: CandidateApplication,
    cv_text: str,
    job_spec_text: str,
    snapshot: dict | None = None,
    pdf_hygiene: dict | None = None,
) -> dict | None:
    """Merge the flag-only cross-source corroboration signals into the score's
    ``integrity_signals`` and triangulate them. Computed here because this is the
    one place with the CV text, the parsed ``cv_sections``, the candidate
    snapshot, the candidate's Workable/social history AND the role JD all in
    scope, so both scoring engines surface them uniformly.

    Layers here are all **$0 / deterministic** and run on every score: JD-shingle
    + CV↔Workable diff + unverified employers (supplementary); years-vs-span
    inflation + tech anachronism (CV-internal coherence); then a triangulation
    summary requiring multiple independent disagreements before "strong_review".

    The **slow** axes — graph collective corroboration and the GitHub URL
    fetch — are deliberately NOT here. They run async + shortlist-gated in
    ``corroboration_enrichment`` (fetching on every score would be the wrong
    placement), and re-triangulate after they land.
    Best-effort — never raises into the scoring path, returns ``existing`` on
    any failure."""
    try:
        from ..platform.config import settings
        from .fraud_detection import (
            aggregate_triangulation,
            build_integrity_warnings,
            build_supplementary_fraud_signals,
            detect_experience_inflation,
            detect_tech_anachronism,
        )

        cand = getattr(application, "candidate", None)
        cv_sections = (
            getattr(application, "cv_sections", None)
            or (getattr(cand, "cv_sections", None) if cand is not None else None)
            or {}
        )
        cv_exp = (
            cv_sections.get("experience") if isinstance(cv_sections, dict) else None
        )
        wk_exp = getattr(cand, "experience_entries", None) if cand is not None else None
        supp = build_supplementary_fraud_signals(
            cv_text=cv_text or "",
            jd_text=job_spec_text or "",
            cv_experience=cv_exp,
            workable_experience=wk_exp,
            shingle_threshold=settings.FRAUD_SHINGLE_THRESHOLD,
            workable_diff_enabled=settings.FRAUD_WORKABLE_DIFF_ENABLED,
        )
        merged = dict(existing or {})
        merged.update(supp)

        # CV-internal coherence (deterministic, flag-only).
        snap = snapshot if isinstance(snapshot, dict) else {}
        timeline = snap.get("timeline") or []
        # Feed the FULL parsed CV history alongside the snapshot timeline (which
        # is capped at the 5 most-recent employers). Without the full list a
        # candidate with >5 jobs has their oldest roles dropped, so the evidenced
        # span looks short and they're wrongly flagged for "inflating" their years.
        infl = detect_experience_inflation(
            snap.get("years_experience"),
            list(timeline) + list(cv_exp or []),
        )
        if infl.triggered:
            merged["experience_inflation"] = infl.to_dict()
        anach = detect_tech_anachronism(cv_exp)
        if anach.triggered:
            merged["tech_anachronism"] = anach.to_dict()

        # Promote the ingest-time PDF-bytes hygiene scan (flag-only) under
        # document_hygiene.pdf, preserving the LLM-path text hygiene already there.
        if isinstance(pdf_hygiene, dict):
            dh = dict(merged.get("document_hygiene") or {})
            dh["pdf"] = pdf_hygiene
            merged["document_hygiene"] = dh

        # Triangulate the deterministic picture — changes no score, adds the
        # verdict + trust band the report reads (and the gate the async
        # enrichment keys off — only flagged high-matches get an enrichment pass).
        merged["triangulation"] = aggregate_triangulation(merged)
        merged["warnings"] = build_integrity_warnings(merged)
        return merged or None
    except Exception:  # pragma: no cover — never break scoring on a flag
        logger.debug("supplementary fraud signals failed", exc_info=True)
        return existing
