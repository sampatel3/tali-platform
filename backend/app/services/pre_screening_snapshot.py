"""Pure score/evidence transforms for pre-screening.

Extracted from ``pre_screening_service`` to keep that module under the
500-LOC architecture gate. These are stateless helpers — no DB writes,
no LLM calls — that shape pre-screen scores and evidence blobs for the
candidate directory and detail views.

``pre_screening_service`` re-exports all of these, so existing import
sites (``from .pre_screening_service import pre_screen_snapshot`` etc.)
keep working unchanged.
"""
from __future__ import annotations

from typing import Any

from ..models.candidate_application import CandidateApplication
from .document_service import sanitize_json_for_storage, sanitize_text_for_storage
from .taali_scoring import normalize_score_100 as _normalize_score_100


def normalize_score_100(value: Any) -> float | None:
    """Coerce a score into the 0-100 range — delegates to the canonical
    ``taali_scoring.normalize_score_100``. Kept as a re-export so the
    existing import surface (and tests) stay stable.

    No implicit ``<= 1.0 → ×100`` or ``<= 10 → ×10`` upscaling: every
    caller passes a value that's already 0-100 by construction
    (cv_match_score, pre_screen_score_100, fraud cap, etc.), and the old
    heuristics silently inflated real weak scores (e.g. ``0.4`` aggregate
    role_fit became ``40``, hiding near-zero candidates as moderate fits;
    the fraud cap of ``10.0`` became ``100`` and pushed plagiarised CVs
    to the top of the rank).
    """
    return _normalize_score_100(value)


def pre_screen_recommendation_label(
    score_100: float | None, threshold: float | None = None
) -> str | None:
    """Map a 0-100 pre-screen score to a recruiter-facing recommendation.

    ``threshold`` is the role's reject cutoff. When supplied, "Below
    threshold" means *below the role's actual cutoff* — not the legacy
    hard-coded ``< 50``. A role that rejects at 30 must not label a
    40-scorer "Below threshold": they're above the bar and just need a
    look ("Manual review recommended"). When ``threshold`` is omitted the
    legacy ``< 50`` boundary is preserved for callers that don't have the
    role in hand (e.g. the bulk directory snapshot fallback).
    """
    if score_100 is None:
        return None
    if threshold is not None and score_100 < float(threshold):
        return "Below threshold"
    if score_100 >= 80.0:
        return "Strong match"
    if score_100 >= 65.0:
        return "Proceed to screening"
    if score_100 >= 50.0:
        return "Manual review recommended"
    # Below the generic review band but at/above the role's reject cutoff:
    # surface for review rather than as a reject verdict. With no threshold
    # in hand, fall back to the legacy "Below threshold" label.
    return "Manual review recommended" if threshold is not None else "Below threshold"


# Raw cv_match ``Recommendation`` enum values that have leaked into the
# recruiter-facing ``pre_screen_recommendation`` (a display field) via the
# snapshot fallback below. Map them to proper labels. ``lean_no`` is
# uncertain → "Manual review recommended" (NOT a hard reject), so we don't
# turn an unsure verdict into a reject card; only a definitive ``no`` →
# "Below threshold".
_CV_RECOMMENDATION_TO_LABEL = {
    "strong_yes": "Strong match",
    "yes": "Proceed to screening",
    "lean_no": "Manual review recommended",
    "no": "Below threshold",
}


def normalize_recommendation_label(value: Any) -> str | None:
    """Coerce a recommendation value into a recruiter-facing label: map a raw
    cv_match enum ('no'/'lean_no'/'yes'/'strong_yes') to its display label,
    pass an already-proper label through unchanged."""
    text = str(value or "").strip()
    if not text:
        return None
    return _CV_RECOMMENDATION_TO_LABEL.get(text.lower(), text)


def build_pre_screen_evidence(details: dict[str, Any] | None) -> dict[str, Any]:
    payload = details if isinstance(details, dict) else {}
    return sanitize_json_for_storage(
        {
            "summary": sanitize_text_for_storage(str(payload.get("summary") or "").strip()) or None,
            "matching_skills": [
                sanitize_text_for_storage(str(item).strip())
                for item in payload.get("matching_skills", [])
                if str(item or "").strip()
            ][:8],
            "missing_skills": [
                sanitize_text_for_storage(str(item).strip())
                for item in payload.get("missing_skills", [])
                if str(item or "").strip()
            ][:8],
            "concerns": [
                sanitize_text_for_storage(str(item).strip())
                for item in payload.get("concerns", [])
                if str(item or "").strip()
            ][:6],
            "score_rationale_bullets": [
                sanitize_text_for_storage(str(item).strip())
                for item in payload.get("score_rationale_bullets", [])
                if str(item or "").strip()
            ][:6],
            "requirements_coverage": payload.get("requirements_coverage")
            if isinstance(payload.get("requirements_coverage"), dict)
            else {},
            "requirements_assessment": payload.get("requirements_assessment")
            if isinstance(payload.get("requirements_assessment"), list)
            else [],
        }
    )


def pre_screen_snapshot(app: CandidateApplication) -> dict[str, Any]:
    details = app.cv_match_details if isinstance(app.cv_match_details, dict) else {}
    cv_fit_score = normalize_score_100(app.cv_match_score)
    requirements_fit_score = normalize_score_100(
        details.get("requirements_match_score_100")
        or details.get("requirements_match_score")
    )
    # Pre-screen and full-score are separate axes.  The old snapshot aliased
    # ``pre_screen_score`` to ``cv_match_score`` and then wrote it back into
    # ``pre_screen_score_100``, contaminating the only value the cheap gate and
    # reject card could read.  Only the durable genuine Stage-1 column is safe.
    # Legacy rows without it remain blank/fail-open instead of guessing from a
    # potentially overwritten shared column or full-score details.
    pre_screen_score = normalize_score_100(
        getattr(app, "genuine_pre_screen_score_100", None)
    )
    recommendation = normalize_recommendation_label(
        getattr(app, "pre_screen_recommendation", None)
    ) or pre_screen_recommendation_label(pre_screen_score)
    recommendation = sanitize_text_for_storage(str(recommendation or "").strip()) or None
    evidence = (
        sanitize_json_for_storage(app.pre_screen_evidence)
        if isinstance(getattr(app, "pre_screen_evidence", None), dict)
        else build_pre_screen_evidence(details)
    )
    return {
        "cv_fit_score": cv_fit_score,
        "requirements_fit_score": requirements_fit_score,
        "pre_screen_score": pre_screen_score,
        "pre_screen_recommendation": recommendation,
        "pre_screen_evidence": evidence,
    }


def refresh_pre_screening_fields(app: CandidateApplication) -> dict[str, Any]:
    snapshot = pre_screen_snapshot(app)
    app.requirements_fit_score_100 = snapshot["requirements_fit_score"]
    app.pre_screen_score_100 = snapshot["pre_screen_score"]
    app.pre_screen_recommendation = snapshot["pre_screen_recommendation"]
    app.pre_screen_evidence = snapshot["pre_screen_evidence"]
    if app.cv_match_score is not None:
        app.rank_score = app.cv_match_score
    elif snapshot["pre_screen_score"] is not None:
        app.rank_score = snapshot["pre_screen_score"]
    elif app.workable_score is not None:
        app.rank_score = app.workable_score
    else:
        app.rank_score = app.cv_match_score
    return snapshot
