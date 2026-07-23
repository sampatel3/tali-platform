"""Build (role_fit_score, label) pairs from recruiter terminal decisions.

Label rule (rejected wins conflicts):
  NEGATIVE  if application_outcome == "rejected" OR workable_disqualified
  POSITIVE  else, if application_outcome == "hired"
                  OR pipeline_stage == "advanced"
                  OR the Workable stage is post-handover (interview/offer/hired)
  EXCLUDED  otherwise (still open / pre-handover — no terminal signal yet)

The score is the logical membership's RAW ``cv_match_score`` (the same value
the decision engine compares the threshold against). We never read a
calibrated value — the objective score stays raw; only the threshold is
learned. Related roles use their independent evaluation score and local
pipeline/outcome; linked ATS-owner state is never a learning label.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...candidate_search.logical_policy_state import (
    LogicalCandidatePolicyMetrics,
    project_logical_candidate_policy_state,
    read_logical_candidate_policy_states,
)
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


def label_for_policy_state(state: LogicalCandidatePolicyMetrics) -> int | None:
    """1 = progressed, 0 = rejected, None = no role-local terminal signal."""

    # Rejected wins all conflicts (e.g. reached an interview stage then
    # rejected). Provider state is present here only for ordinary membership;
    # the canonical projection suppresses related-role ATS-owner judgments.
    if state.application_outcome == "rejected" or state.local_disqualified:
        return 0
    if state.application_outcome == "hired":
        return 1
    if state.pipeline_stage == "advanced":
        return 1
    if is_post_handover_workable_stage(state.local_external_stage):
        return 1
    return None


def label_for_application(app: CandidateApplication) -> int | None:
    """1 = recruiter advanced/hired, 0 = rejected, None = no terminal signal."""

    return label_for_policy_state(project_logical_candidate_policy_state(app))


def build_labelled_pairs(
    db: Session, *, organization_id: int, role_id: int | None = None
) -> LabelledSet:
    """Pull labelled (raw role_fit, label) pairs for an org (or one role)."""
    states = read_logical_candidate_policy_states(
        db,
        organization_id=int(organization_id),
        role_ids=((int(role_id),) if role_id is not None else ()),
    )

    pairs: list[tuple[float, int]] = []
    pos = neg = 0
    pv_counts: dict[str, int] = {}
    for state in states:
        label = label_for_policy_state(state)
        if label is None:
            continue
        score = state.cv_match_score
        if score is None:
            continue
        pairs.append((score, label))
        if label == 1:
            pos += 1
        else:
            neg += 1
        pv = state.prompt_version
        if pv:
            pv_counts[pv] = pv_counts.get(pv, 0) + 1

    dominant = max(pv_counts, key=pv_counts.get) if pv_counts else None
    return LabelledSet(
        pairs=pairs, n_positive=pos, n_negative=neg, prompt_version=dominant
    )
