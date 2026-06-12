"""Build (role_fit_score, label) pairs from recruiter terminal decisions.

Label rule (rejected wins conflicts):
  NEGATIVE  if application_outcome == "rejected" OR workable_disqualified
  POSITIVE  else, if application_outcome == "hired"
                  OR pipeline_stage == "advanced"
                  OR the Workable stage is post-handover (interview/offer/hired)
  EXCLUDED  otherwise (still open / pre-handover — no terminal signal yet)

The score is the RAW ``cv_match_score`` (the same value the decision engine
compares the threshold against). We never read a calibrated value — the
objective score stays raw; only the threshold is learned.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...domains.assessments_runtime.pipeline_service import (
    is_post_handover_workable_stage,
)
from ...models.candidate_application import CandidateApplication


@dataclass
class LabelledSet:
    pairs: list[tuple[float, int]]  # (role_fit_score 0..100, label 1=advanced/0=rejected)
    n_positive: int
    n_negative: int
    prompt_version: str | None  # dominant scoring-prompt cohort among the labelled apps

    @property
    def n_total(self) -> int:
        return self.n_positive + self.n_negative


def label_for_application(app: CandidateApplication) -> int | None:
    """1 = recruiter advanced/hired, 0 = rejected, None = no terminal signal."""
    outcome = (app.application_outcome or "").strip().lower()
    # Rejected wins all conflicts (e.g. reached an interview stage then rejected).
    if outcome == "rejected" or bool(getattr(app, "workable_disqualified", False)):
        return 0
    if outcome == "hired":
        return 1
    if (app.pipeline_stage or "").strip().lower() == "advanced":
        return 1
    if is_post_handover_workable_stage(app.workable_stage):
        return 1
    return None


def build_labelled_pairs(
    db: Session, *, organization_id: int, role_id: int | None = None
) -> LabelledSet:
    """Pull labelled (raw role_fit, label) pairs for an org (or one role)."""
    q = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == organization_id,
        CandidateApplication.deleted_at.is_(None),
        CandidateApplication.cv_match_score.isnot(None),
    )
    if role_id is not None:
        q = q.filter(CandidateApplication.role_id == role_id)

    pairs: list[tuple[float, int]] = []
    pos = neg = 0
    pv_counts: dict[str, int] = {}
    for app in q.all():
        label = label_for_application(app)
        if label is None:
            continue
        try:
            score = float(app.cv_match_score)
        except (TypeError, ValueError):
            continue
        pairs.append((score, label))
        if label == 1:
            pos += 1
        else:
            neg += 1
        det = app.cv_match_details if isinstance(app.cv_match_details, dict) else {}
        pv = det.get("prompt_version")
        if pv:
            pv_counts[pv] = pv_counts.get(pv, 0) + 1

    dominant = max(pv_counts, key=pv_counts.get) if pv_counts else None
    return LabelledSet(
        pairs=pairs, n_positive=pos, n_negative=neg, prompt_version=dominant
    )
