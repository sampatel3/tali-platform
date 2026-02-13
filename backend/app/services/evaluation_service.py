from __future__ import annotations

from typing import Dict, Any


SCORE_TO_NUMERIC = {"poor": 1, "good": 2, "excellent": 3}


def calculate_weighted_rubric_score(category_scores: Dict[str, str], evaluation_rubric: Dict[str, Any]) -> float:
    total = 0.0
    weight_total = 0.0
    for category, grade in category_scores.items():
        weight = float((evaluation_rubric.get(category) or {}).get("weight", 0.0) or 0.0)
        score = SCORE_TO_NUMERIC.get(str(grade).lower())
        if score is None:
            continue
        total += score * weight
        weight_total += weight
    if weight_total <= 0:
        return 0.0
    return round(total / weight_total, 4)
