from __future__ import annotations

from typing import Any

from ..platform.config import settings

TAALI_SCORING_RUBRIC_VERSION = "taali_v3_role_fit_blended"


def _role_fit_weights() -> dict[str, float]:
    return {
        "cv_fit": float(settings.TAALI_WEIGHT_CV_FIT),
        "requirements_fit": float(settings.TAALI_WEIGHT_REQUIREMENTS_FIT),
    }


def _taali_weights() -> dict[str, float]:
    return {
        "assessment": float(settings.TAALI_WEIGHT_ASSESSMENT),
        "role_fit": float(settings.TAALI_WEIGHT_ROLE_FIT),
    }


# Public dict-shaped views for callers that read weights for breakdown payloads.
# These are recomputed on each access so settings overrides take effect without
# a process restart at module import time.
class _WeightView(dict):
    def __init__(self, getter):
        self._getter = getter
        super().__init__(getter())

    def __getitem__(self, key):
        return self._getter()[key]

    def get(self, key, default=None):
        return self._getter().get(key, default)


ROLE_FIT_WEIGHTS = _WeightView(_role_fit_weights)
TAALI_WEIGHTS = _WeightView(_taali_weights)


def normalize_score_100(value: Any) -> float | None:
    """Coerce a score into the 0-100 range.

    Auto-scales values in the (0, 1.0] range up to the 0-100 scale —
    these are clearly 0-1 fractions. Everything else is treated as
    already on 0-100 and just clamped. The previous heuristic auto-scaled
    anything ``<= 10`` which silently inflated *real* weak 0-100 scores
    (e.g. a candidate's ``role_fit_score`` of 9.6 became 96, masking a
    weak-fit candidate as a top one). Pre-screen, cv_match, taali, and
    role-fit columns are all documented 0-100 by column name, so the
    auto-scale isn't needed at all for those — just clamp.
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    if 0 < numeric <= 1.0:
        numeric *= 100.0
    return round(max(0.0, min(100.0, numeric)), 1)


def weighted_average_100(*weighted_values: tuple[float | None, float]) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for value, weight in weighted_values:
        normalized_value = normalize_score_100(value)
        try:
            normalized_weight = float(weight)
        except (TypeError, ValueError):
            continue
        if normalized_value is None or normalized_weight <= 0:
            continue
        numerator += normalized_value * normalized_weight
        denominator += normalized_weight
    if denominator <= 0:
        return None
    return round(numerator / denominator, 1)


def compute_role_fit_score(cv_fit_score: Any, requirements_fit_score: Any) -> float | None:
    return weighted_average_100(
        (cv_fit_score, ROLE_FIT_WEIGHTS["cv_fit"]),
        (requirements_fit_score, ROLE_FIT_WEIGHTS["requirements_fit"]),
    )


def compute_taali_score(assessment_score: Any, role_fit_score: Any) -> float | None:
    return weighted_average_100(
        (assessment_score, TAALI_WEIGHTS["assessment"]),
        (role_fit_score, TAALI_WEIGHTS["role_fit"]),
    )
