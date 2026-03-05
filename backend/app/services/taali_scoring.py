from __future__ import annotations

from typing import Any

TAALI_SCORING_RUBRIC_VERSION = "taali_v3_role_fit_blended"
ROLE_FIT_WEIGHTS = {"cv_fit": 0.5, "requirements_fit": 0.5}
TAALI_WEIGHTS = {"assessment": 0.5, "role_fit": 0.5}


def normalize_score_100(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
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
