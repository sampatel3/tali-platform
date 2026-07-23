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

from ..candidate_search.logical_policy_state import (
    read_logical_candidate_policy_metrics,
)
from ..models.role import Role


_LABELLED_MIN_SAMPLES = 5
_DISTRIBUTION_PERCENTILE = 30  # bottom-third floor
_THRESHOLD_FLOOR = 30
_THRESHOLD_CEILING = 75
_DEFAULT_FALLBACK = 50

# --- Role-fit SEND threshold (the post-pre-screen send/advance bar) ----------
# A GENERAL, ABSOLUTE quality bar on the role-fit (CV-match) score — NOT a
# per-role percentile. It is derived once from the ORG-WIDE score
# distribution (all roles pooled) and applied identically to every role.
#
# Why absolute, not per-role: a per-role "top N%" forces a fixed share
# through even when a whole role's pool is weak ("don't push a group of
# rubbish candidates into interviews just because they're the best of a bad
# bunch"). With one global bar, a strong role sends many and a weak role
# sends few/none — the right behaviour falls out for free.
#
# Level: ~top 20% of the org-wide distribution. On a normal/strong role that
# lands around 5–10% of that role's applicants reaching HITL (e.g. ~24 of
# 377 for a healthy pipeline); a weak role sends ~none. Recomputed live, so
# it tracks the org's candidate quality over time. Floor keeps a genuine
# minimum even if the whole org pipeline is weak.
_SEND_GLOBAL_TARGET_PCT = 20.0  # bar = ~top fifth of the ORG-WIDE distribution
_SEND_FLOOR = 50                # absolute quality minimum for a SEND bar
_SEND_CEILING = 85
_SEND_FALLBACK = 55             # no scored candidates yet


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
    states = read_logical_candidate_policy_metrics(
        db,
        organization_id=int(role.organization_id),
        role_ids=(int(role.id),),
    )
    out: list[float] = []
    progressed_stages = {"invited", "in_assessment", "review", "advanced"}
    for state in states:
        if (
            state.application_outcome != "hired"
            and state.pipeline_stage not in progressed_stages
        ):
            continue
        pre_score = state.pre_screen_score
        match_score = state.cv_match_score
        if pre_score is not None:
            out.append(float(pre_score))
        elif match_score is not None:
            out.append(float(match_score))
    return out


def _scored_distribution(db: Session, *, role: Role) -> list[float]:
    """All pre-screen scores for the role's currently-scored pool."""
    states = read_logical_candidate_policy_metrics(
        db,
        organization_id=int(role.organization_id),
        role_ids=(int(role.id),),
    )
    return [
        float(state.pre_screen_score)
        for state in states
        if state.pre_screen_score is not None
    ]


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


def _org_scored_role_fit(db: Session, *, organization_id: int) -> list[float]:
    """Role-fit scores of every scored logical membership in the organization.

    Owner and related memberships for one candidate remain independent rows
    with independent scores. This pool defines the general, absolute quality
    bar and is deliberately not filtered to one role.
    """
    states = read_logical_candidate_policy_metrics(
        db,
        organization_id=int(organization_id),
    )
    return [
        float(state.cv_match_score)
        for state in states
        if state.cv_match_score is not None
    ]


def compute_role_fit_send_threshold(
    db: Session, *, role: Role
) -> ThresholdRecommendation:
    """General, ABSOLUTE send/advance bar on the role-fit score.

    Derived from the ORG-WIDE score distribution (all roles pooled), NOT a
    per-role percentile: the bar is the ~top ``_SEND_GLOBAL_TARGET_PCT``%
    of every candidate the org has scored, then applied identically to all
    roles. So a strong role sends many candidates and a weak role sends
    few/none — a pool of all-weak candidates is never forced to surface its
    "best of a bad bunch". Recomputed live, so it tracks org candidate
    quality over time. Clamped to ``[_SEND_FLOOR, _SEND_CEILING]`` so there
    is always a genuine minimum even if the whole pipeline is weak.

    Note ``role`` is used only for its ``organization_id`` — the value is
    identical for every role in the org by design."""
    pool = _org_scored_role_fit(db, organization_id=int(role.organization_id))
    if not pool:
        return ThresholdRecommendation(
            value=_SEND_FLOOR, source="fallback",
            rationale=(
                "No scored candidates in the org yet — using the minimum send "
                f"bar of {_SEND_FLOOR}. It recalibrates as candidates score."
            ),
            sample_size=0,
        )

    raw = _percentile(pool, 100 - _SEND_GLOBAL_TARGET_PCT)  # ~top 20% org-wide
    value = int(max(_SEND_FLOOR, min(_SEND_CEILING, round(raw))))
    return ThresholdRecommendation(
        value=value,
        source="global_absolute",
        rationale=(
            f"General quality bar = top ~{_SEND_GLOBAL_TARGET_PCT:.0f}% of the "
            f"{len(pool)} candidates scored across the org → send bar {value}. "
            "Applied to every role, so weak pipelines send few and strong ones "
            "send more."
        ),
        sample_size=len(pool),
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


def _active_learned_threshold(db: Session, *, role: Role) -> float | None:
    """The approved, learned-from-recruiter-decisions threshold for this role.

    Prefers a role-scoped active ``ThresholdCalibration`` row, else the
    org-wide one. ``None`` when no calibration has been activated — the caller
    then falls back to the volume heuristic. Read defensively so a calibration
    lookup never breaks the verdict path.
    """
    try:
        from ..models.threshold_calibration import STATUS_ACTIVE, ThresholdCalibration

        base = db.query(ThresholdCalibration).filter(
            ThresholdCalibration.organization_id == role.organization_id,
            ThresholdCalibration.status == STATUS_ACTIVE,
        )
        role_row = (
            base.filter(ThresholdCalibration.role_id == role.id)
            .order_by(ThresholdCalibration.activated_at.desc())
            .first()
        )
        if role_row is not None:
            return float(role_row.learned_threshold)
        org_row = (
            base.filter(ThresholdCalibration.role_id.is_(None))
            .order_by(ThresholdCalibration.activated_at.desc())
            .first()
        )
        if org_row is not None:
            return float(org_row.learned_threshold)
    except Exception:  # pragma: no cover — never break the verdict path
        return None
    return None


def resolve_role_fit_threshold(db: Session, *, role: Role) -> float | None:
    """The single role-fit send/reject boundary the decision engine uses.

    Precedence:
      1. Manual mode + a recruiter-set ``score_threshold`` → that value wins
         (unchanged — including an explicit 0).
      2. Otherwise (auto mode, or manual with no value set): an approved,
         learned-from-recruiter-decisions threshold (``ThresholdCalibration``,
         role-scoped then org-scoped) — this REPLACES the volume heuristic once
         a recruiter has activated a calibration.
      3. Else the dynamic ``compute_role_fit_send_threshold`` volume heuristic.

    The learned value only slots in where the heuristic was already in play, so
    activating a calibration never changes a role the recruiter pinned manually.
    Returns None only if nothing is computable (engine keeps stored thresholds).
    """
    try:
        mode = getattr(role, "auto_reject_threshold_mode", None) or "manual"
        if mode != "auto" and role.score_threshold is not None:
            return float(role.score_threshold)
        learned = _active_learned_threshold(db, role=role)
        if learned is not None:
            return float(learned)
        return float(compute_role_fit_send_threshold(db, role=role).value)
    except Exception:  # pragma: no cover — never break the verdict path
        return None
