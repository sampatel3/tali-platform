"""Shared assessment status / completion / percentile / score primitives.

These helpers were duplicated across
``domains/assessments_runtime/analytics_routes.py`` and
``services/candidate_feedback_engine.py``. They now live here as the single
source of truth so the analytics dashboards and the client report PDFs can
never disagree on the numbers they derive from the same assessment row.

Score-derivation policy (unified 2026-06-26)
--------------------------------------------
``score_100`` resolves the overall score in the order
``taali_score → assessment_score → final_score → score×10``. ``taali_score`` is
first on purpose: it is the blended headline the client report PDF prints as
"TAALI score" (see ``candidate_feedback_engine._assessment_score_components_100``)
and the figure the analytics dashboards surface — so both read the *same* number
by construction.

The two former copies had drifted:
  * analytics used this ``taali_score`` fallback chain but returned the raw
    column value (no clamping);
  * the feedback engine's copy read ``final_score`` only and fed the benchmark
    percentile, so the PDF's "Top N%" was computed on a *different* basis than
    the PDF's own headline.
Folding ``final_score``-only in would have made analytics disagree with the PDF
headline (the opposite of the goal), so the canonical chain is the ``taali``-first
one, hardened with the feedback engine's non-inflating clamp: ``_coerce_score_100``
mirrors ``services.taali_scoring.normalize_score_100`` — clamp to 0-100, round to
1dp, and *never* apply the legacy ``<=10 → ×10`` upscaling that silently inflated
genuinely-weak scores. ``extract_category_scores`` rounds dimension values to 2dp
(the feedback engine's behaviour; a no-op for the analytics averages).
"""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import and_, or_

from ...models.assessment import Assessment, AssessmentStatus


# Canonical dimension keys plus the legacy aliases seen in persisted
# ``score_breakdown`` / ``prompt_analytics`` payloads. Shared so both surfaces
# canonicalize category scores identically.
_DIMENSION_ALIASES = {
    "task_completion": "task_completion",
    "prompt_clarity": "prompt_clarity",
    "context_provision": "context_provision",
    "independence_efficiency": "independence_efficiency",
    "response_utilization": "response_utilization",
    "debugging_design": "debugging_design",
    "written_communication": "written_communication",
    "role_fit": "role_fit",
    # Legacy aliases seen in older payloads.
    "independence": "independence_efficiency",
    "utilization": "response_utilization",
    "communication": "written_communication",
    "approach": "debugging_design",
    "cv_match": "role_fit",
}


def status_value(assessment: Assessment) -> str:
    raw = getattr(assessment.status, "value", assessment.status)
    return str(raw or "").lower()


def is_completed(assessment: Assessment) -> bool:
    return status_value(assessment) in {
        AssessmentStatus.COMPLETED.value,
        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT.value,
    }


def completed_assessment_filter():
    """SQLAlchemy filter for assessments that genuinely completed (incl. timeout)."""
    return and_(
        Assessment.completed_at.isnot(None),
        Assessment.is_voided.is_(False),
        or_(
            Assessment.status == AssessmentStatus.COMPLETED,
            Assessment.completed_due_to_timeout.is_(True),
        ),
    )


def percentile_rank(values: Sequence[float], target: float) -> float:
    if not values:
        return 0.0
    count = sum(1 for value in values if value <= target)
    return round((count / len(values)) * 100.0, 1)


def _coerce_score_100(value: object) -> float | None:
    """Clamp a raw score column to 0-100 with NO implicit upscaling.

    Mirrors ``services.taali_scoring.normalize_score_100`` (kept inline so this
    low-level primitives module needn't depend on the services layer): negative
    is treated as missing, values are clamped to [0, 100] and rounded to 1dp.
    The legacy ``<=10 → ×10`` heuristic is deliberately NOT applied — it
    silently inflated genuinely-weak scores.
    """
    if not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if numeric < 0:
        return None
    return round(max(0.0, min(100.0, numeric)), 1)


def score_100(assessment: Assessment) -> float | None:
    """Canonical 0-100 overall score for an assessment.

    Resolution order: ``taali_score`` (blended headline) → ``assessment_score``
    → ``final_score`` → legacy ``score`` (0-10, rescaled ×10). See the module
    docstring for why ``taali_score`` leads.
    """
    taali_score = getattr(assessment, "taali_score", None)
    if isinstance(taali_score, (int, float)):
        return _coerce_score_100(taali_score)
    assessment_score = getattr(assessment, "assessment_score", None)
    if isinstance(assessment_score, (int, float)):
        return _coerce_score_100(assessment_score)
    final_score = getattr(assessment, "final_score", None)
    if isinstance(final_score, (int, float)):
        return _coerce_score_100(final_score)
    score = getattr(assessment, "score", None)
    if isinstance(score, (int, float)):
        # Legacy ``score`` is the 0-10 column → rescale to 0-100. This is a
        # definitional rescale, not the banned ``<=10`` inflation heuristic.
        return _coerce_score_100(float(score) * 10.0)
    return None


def score_10(assessment: Assessment) -> float | None:
    """Canonical 0-10 overall score (``score_100`` / 10), legacy column last."""
    value_100 = score_100(assessment)
    if value_100 is not None:
        return value_100 / 10.0
    score = getattr(assessment, "score", None)
    if isinstance(score, (int, float)):
        return float(score)
    return None


def extract_category_scores(assessment: Assessment) -> dict[str, float]:
    """Canonicalized per-dimension scores from the stored breakdown/analytics.

    Reads ``score_breakdown.category_scores`` first, then the prompt-analytics
    fallbacks, maps legacy aliases to canonical keys, and rounds to 2dp.
    """
    breakdown = assessment.score_breakdown if isinstance(assessment.score_breakdown, dict) else {}
    analytics = assessment.prompt_analytics if isinstance(assessment.prompt_analytics, dict) else {}

    raw_scores = (
        (breakdown.get("category_scores") if isinstance(breakdown.get("category_scores"), dict) else None)
        or (analytics.get("category_scores") if isinstance(analytics.get("category_scores"), dict) else None)
        or (
            analytics.get("detailed_scores", {}).get("category_scores")
            if isinstance(analytics.get("detailed_scores"), dict)
            and isinstance(analytics.get("detailed_scores", {}).get("category_scores"), dict)
            else None
        )
        or {}
    )

    out: dict[str, float] = {}
    for key, raw_value in raw_scores.items():
        canonical = _DIMENSION_ALIASES.get(str(key))
        if not canonical or not isinstance(raw_value, (int, float)):
            continue
        out[canonical] = round(float(raw_value), 2)
    return out
