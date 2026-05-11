"""Recommend a pre-screen reject threshold for a role.

The recruiter has no good prior for "what number do I put here?" — so we
compute one. The recommendation has three tiers in priority order:

1. **Labelled** — when the role has >= 5 advanced/hired candidates,
   anchor on those: ``median(advanced_scores) - 1σ``. The labelled data
   is the ground truth: candidates the recruiter actually decided to
   progress, so the threshold should sit comfortably below the worst
   of them.

2. **Distribution** — when no labels yet, use the role's scored CV
   distribution: take the 30th percentile (cut the bottom third). This
   matches the cheap-cleanup intent without committing to a number
   that locks out half the pool.

3. **Fixed floor** — when no scored candidates either, fall back to a
   sensible default (50). The agent runs once and switches to tier 1 /
   tier 2 the next cycle.

In all cases the recommendation is clamped to ``[30, 75]`` to avoid
edge cases (a role with a single 12-scoring advance shouldn't drag the
threshold down to single digits; a role with all 90+s shouldn't push
the threshold so high every new applicant is rejected).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.role import Role


_LABELLED_MIN_SAMPLES = 5
_DISTRIBUTION_PERCENTILE = 30  # bottom-third floor
_THRESHOLD_FLOOR = 30
_THRESHOLD_CEILING = 75
_DEFAULT_FALLBACK = 50


@dataclass
class ThresholdRecommendation:
    value: int
    source: str  # "labelled" | "distribution" | "fallback"
    rationale: str
    sample_size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": int(self.value),
            "source": self.source,
            "rationale": self.rationale,
            "sample_size": int(self.sample_size),
        }


def _clamp(value: float) -> int:
    return int(max(_THRESHOLD_FLOOR, min(_THRESHOLD_CEILING, round(value))))


def _advanced_scores(db: Session, *, role: Role) -> list[float]:
    """CV-match scores for candidates the recruiter actually progressed.

    Pulls from ``CandidateApplication.pre_screen_score_100`` first (the
    signal the threshold compares against) for any candidate that
    reached an interview stage or hire outcome. Falls back to
    ``cv_match_score`` when pre-screen score is missing.
    """
    rows = (
        db.query(
            CandidateApplication.pre_screen_score_100,
            CandidateApplication.cv_match_score,
        )
        .filter(
            CandidateApplication.role_id == role.id,
            CandidateApplication.organization_id == role.organization_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .filter(
            (CandidateApplication.application_outcome == "hired")
            | CandidateApplication.pipeline_stage.in_(
                ("invited", "in_assessment", "review", "technical_interview")
            )
        )
        .all()
    )
    out: list[float] = []
    for pre_score, match_score in rows:
        if pre_score is not None:
            out.append(float(pre_score))
        elif match_score is not None:
            out.append(float(match_score))
    return out


def _scored_distribution(db: Session, *, role: Role) -> list[float]:
    """All pre-screen scores for the role's currently-scored pool."""
    rows = (
        db.query(CandidateApplication.pre_screen_score_100)
        .filter(
            CandidateApplication.role_id == role.id,
            CandidateApplication.organization_id == role.organization_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.pre_screen_score_100.isnot(None),
        )
        .all()
    )
    return [float(r[0]) for r in rows if r[0] is not None]


def _percentile(values: list[float], pct: int) -> float:
    """Linear-interpolated percentile (Excel-style, sample). Cheap; we
    avoid pulling in numpy for one call per threshold suggestion.
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def compute_recommended_threshold(
    db: Session, *, role: Role
) -> ThresholdRecommendation:
    """Pick a threshold for the role using the best available signal."""
    advanced = _advanced_scores(db, role=role)
    if len(advanced) >= _LABELLED_MIN_SAMPLES:
        med = statistics.median(advanced)
        sd = statistics.pstdev(advanced) if len(advanced) > 1 else 0.0
        raw = med - sd
        clamped = _clamp(raw)
        return ThresholdRecommendation(
            value=clamped,
            source="labelled",
            rationale=(
                f"{len(advanced)} candidates have advanced to interview "
                f"or beyond on this role. Their median pre-screen score is "
                f"{med:.0f}; one standard deviation below that lands at "
                f"{int(round(raw))}. Anchoring there keeps the threshold "
                f"safely under the worst recruiter-progressed candidate."
            ),
            sample_size=len(advanced),
        )

    distribution = _scored_distribution(db, role=role)
    if distribution:
        pct = _percentile(distribution, _DISTRIBUTION_PERCENTILE)
        clamped = _clamp(pct)
        return ThresholdRecommendation(
            value=clamped,
            source="distribution",
            rationale=(
                f"No advance/hire labels yet, so anchoring on the score "
                f"distribution: the {_DISTRIBUTION_PERCENTILE}th percentile "
                f"of {len(distribution)} scored candidates is "
                f"{int(round(pct))}. This cuts the obvious bottom slice "
                f"without committing to a number that hides half the pool."
            ),
            sample_size=len(distribution),
        )

    return ThresholdRecommendation(
        value=_DEFAULT_FALLBACK,
        source="fallback",
        rationale=(
            "No scored candidates yet on this role — using the default "
            f"{_DEFAULT_FALLBACK}. The recommendation will refresh once "
            "candidates are pre-screened."
        ),
        sample_size=0,
    )


def effective_threshold(
    db: Session, *, role: Role
) -> int | None:
    """Resolve the role's runtime threshold, honouring ``auto`` mode.

    Returns the integer threshold to use, or ``None`` when neither a
    manual value nor a computable recommendation is available.
    """
    mode = getattr(role, "auto_reject_threshold_mode", None) or "manual"
    if mode == "auto":
        return compute_recommended_threshold(db, role=role).value
    return role.score_threshold
