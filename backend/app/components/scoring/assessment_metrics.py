"""Shared assessment status / completion / percentile primitives.

These four helpers were duplicated verbatim in
``domains/assessments_runtime/analytics_routes.py`` and
``services/candidate_feedback_engine.py``. They are behaviour-identical and
side-effect free, so they live here as the single source of truth.

Note: the *score-derivation* helpers (``_score_100``/``_score_10``/
``_extract_category_scores``) intentionally still live per-file — they have
**drifted** between the two call sites (analytics keeps the legacy
``taali_score``/``assessment_score`` fallbacks and does not round; the feedback
engine uses ``final_score`` only and rounds to 2dp to avoid weak-score
inflation). Reconciling those changes live report/analytics numbers, so it is a
deliberate scoring decision, not a mechanical dedup — do not fold them in here
without that call being made.
"""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import and_, or_

from ...models.assessment import Assessment, AssessmentStatus


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
