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

# --- Role-fit SEND threshold (the post-pre-screen send/advance bar) ----------
# Different intent from the pre-screen reject floor above: this gates the
# detailed role-fit (CV-match) score and decides who reaches HITL review
# (send_assessment / advance). Two signals, balanced:
#   (a) quality — role-fit scores of candidates the recruiter actually
#       progressed to interview / offer / hire (strong signals), and
#   (b) volume — keep HITL review to ~5–10% of the scored pool.
# 5–10% above the bar maps to the 90th–95th percentile of role-fit, so the
# volume band is the hard guardrail; the quality anchor positions the bar
# inside it. Strong-signal scores are noisy (some interviewees score low),
# so volume dominates when they'd drag the bar too low.
_SEND_STRONG_STAGES = ("Technical Interview", "Final Interview", "Offer", "Hired")
_SEND_STRONG_STAGES_NORM = ("technical_interview", "final_interview", "offer", "hired")
_SEND_VOLUME_TARGET_PCT = 7.5   # midpoint of the 5–10% review band
_SEND_VOLUME_CAP_PCT = 10.0     # never send more than ~this share -> p90
_SEND_VOLUME_FLOOR_PCT = 5.0    # never send fewer than ~this share -> p95
_SEND_MIN_STRONG_SAMPLES = 3
_SEND_FLOOR = 45                # absolute safety floor for a SEND bar
_SEND_CEILING = 85
_SEND_FALLBACK = 60             # no scored candidates yet


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
                ("invited", "in_assessment", "review", "advanced")
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


def _role_fit_pool(db: Session, *, role: Role) -> list[float]:
    """Role-fit (CV-match) scores for the role's scored, pre-screen-passing
    pool — the candidates eligible to reach HITL review. This is the base
    the volume cap (5–10%) is measured against."""
    rows = (
        db.query(CandidateApplication.cv_match_score)
        .filter(
            CandidateApplication.role_id == role.id,
            CandidateApplication.organization_id == role.organization_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.cv_match_score.isnot(None),
            (
                (CandidateApplication.pre_screen_score_100.is_(None))
                | (CandidateApplication.pre_screen_score_100 >= 50)
            ),
        )
        .all()
    )
    return [float(r[0]) for r in rows if r[0] is not None]


def _strong_signal_role_fit(db: Session, *, role: Role) -> list[float]:
    """Role-fit scores of candidates the recruiter actually progressed —
    reached technical/final interview, offer, or hire. These are the
    strongest available 'this candidate was worth pursuing' signals.

    Computed GLOBALLY across the org (all roles), not per-role: a strong
    role-fit score means roughly the same thing regardless of role, and
    many roles have few/no interview-stage candidates of their own, so a
    per-role anchor would be noisy or empty. Pooling org-wide gives a
    stable anchor every role shares. (Scoped to the org — never across
    tenants.) The volume cap stays per-role."""
    rows = (
        db.query(CandidateApplication.cv_match_score)
        .filter(
            CandidateApplication.organization_id == role.organization_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.cv_match_score.isnot(None),
            (
                (CandidateApplication.application_outcome == "hired")
                | (CandidateApplication.workable_stage.in_(_SEND_STRONG_STAGES))
                | (CandidateApplication.external_stage_normalized.in_(_SEND_STRONG_STAGES_NORM))
            ),
        )
        .all()
    )
    return [float(r[0]) for r in rows if r[0] is not None]


def compute_role_fit_send_threshold(
    db: Session, *, role: Role
) -> ThresholdRecommendation:
    """Dynamic send/advance bar on the role-fit score.

    Balances (a) the role-fit scores of recruiter-progressed candidates
    (interview/offer/hire) with (b) a 5–10% HITL-review volume cap. The
    volume band maps to the 90th–95th percentile of the scored pool and is
    the hard guardrail; the strong-signal median positions the bar inside
    it. Recomputed live each evaluation, so it tracks the data daily with
    no separate job. Clamped to [45, 85]."""
    pool = _role_fit_pool(db, role=role)
    if not pool:
        return ThresholdRecommendation(
            value=_SEND_FALLBACK, source="fallback",
            rationale=(
                "No scored candidates yet — using a sensible default send bar "
                f"of {_SEND_FALLBACK}. It will recalibrate as candidates score."
            ),
            sample_size=0,
        )

    p_cap = _percentile(pool, 100 - _SEND_VOLUME_CAP_PCT)     # ~p90 -> at most ~10% above
    p_floor = _percentile(pool, 100 - _SEND_VOLUME_FLOOR_PCT)  # ~p95 -> at least ~5% above
    target = _percentile(pool, 100 - _SEND_VOLUME_TARGET_PCT)  # ~p92.5 -> ~7.5%

    strong = _strong_signal_role_fit(db, role=role)
    if len(strong) >= _SEND_MIN_STRONG_SAMPLES:
        strong_med = statistics.median(strong)
        # Don't set the bar above the median progressed candidate (we still
        # want to surface candidates as strong as those interviewed), but
        # keep volume inside the 5–10% band.
        value = min(target, strong_med)
        value = max(value, p_cap)   # cap volume at ~10%
        value = min(value, p_floor)  # ensure at least ~5% flow
        source = "labelled_volume_balanced"
        rationale = (
            f"{len(strong)} candidates reached interview/offer/hire (median "
            f"role-fit {strong_med:.0f}); balanced against a 5–10% review-volume "
            f"cap over {len(pool)} scored candidates → send bar {round(value)}."
        )
    else:
        value = target
        value = max(value, p_cap)
        value = min(value, p_floor)
        source = "volume"
        rationale = (
            f"No interview/offer/hire labels yet; targeting ~{_SEND_VOLUME_TARGET_PCT:.0f}% "
            f"of {len(pool)} scored candidates for review → send bar {round(value)}."
        )

    value = int(max(_SEND_FLOOR, min(_SEND_CEILING, round(value))))
    # Enforce the volume cap strictly even when scores are tied at the
    # boundary (a percentile can land inside a big cluster, so `>= value`
    # would sweep far more than the cap). Raise the bar past the cluster —
    # prefer sending fewer than flooding the review queue.
    cap_count = max(1, int(round(_SEND_VOLUME_CAP_PCT / 100.0 * len(pool))))
    while value < _SEND_CEILING and sum(1 for s in pool if s >= value) > cap_count:
        value += 1
    return ThresholdRecommendation(
        value=value, source=source, rationale=rationale, sample_size=len(pool)
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


def resolve_role_fit_threshold(db: Session, *, role: Role) -> float | None:
    """The single role-fit send/reject boundary the decision engine uses.

    Auto mode → the dynamic, agent-managed ``compute_role_fit_send_threshold``
    (strong-stage anchor + 5–10% volume cap, recomputed live). Manual mode →
    the recruiter's fixed ``score_threshold``, falling back to the dynamic
    value when they haven't set one — so EVERY candidate lands on one side of
    the boundary and gets a decision (no silent "gap"). Returns None only if
    nothing is computable, in which case the engine keeps the stored policy
    thresholds unchanged.
    """
    try:
        mode = getattr(role, "auto_reject_threshold_mode", None) or "manual"
        if mode == "auto":
            return float(compute_role_fit_send_threshold(db, role=role).value)
        # Manual mode: recruiter's fixed value, else the dynamic recommendation.
        if role.score_threshold is not None:
            return float(role.score_threshold)
        return float(compute_role_fit_send_threshold(db, role=role).value)
    except Exception:  # pragma: no cover — never break the verdict path
        return None
