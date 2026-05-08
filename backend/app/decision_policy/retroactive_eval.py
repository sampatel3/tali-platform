"""Retroactively evaluate the current policy against a manual event.

For each manual recruiter ``CandidateApplicationEvent``, reconstruct
``DecisionInputs`` *as of the event time* (using scores cached on the
application — we do NOT backfill scoring just for retune) and run
``engine.evaluate`` against the *current active policy*. Compare the
verdict to what the recruiter actually did to produce a
``disagreement_pattern``.

The four patterns in §5.2 of AGENTIC_DECISION_SYSTEM.md:

  - ``manual-send-on-would-reject``: recruiter sent assessment,
    policy would have queued reject (or skip-assessment-reject).
  - ``manual-reject-on-would-send``: recruiter rejected, policy
    would have queued send_assessment.
  - ``manual-advance-on-would-reject-post-assessment``: recruiter
    advanced, policy would have queued reject (post-assessment).
  - ``manual-reject-on-would-advance``: recruiter rejected, policy
    would have queued advance.

Anything else (e.g. policy and recruiter agreed) → ``"agreement"``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.role import Role
from .engine import DecisionInputs, evaluate


logger = logging.getLogger("taali.decision_policy.retroactive_eval")


@dataclass
class Disagreement:
    pattern: str
    decision_point: str | None
    policy_decision_type: str
    recruiter_kind: str


# Recruiter event_type → categorical "what did the recruiter do".
_RECRUITER_KIND: dict[str, str] = {
    "assessment_invite_sent": "send",
    "assessment_invite_resent": "send",
    "assessment_retake_sent": "send",
    "auto_rejected": "reject",
    "workable_disqualified": "reject",
}


def _classify_event(event: CandidateApplicationEvent) -> str | None:
    """Return one of {'send', 'reject', 'advance'} or None."""
    if event.actor_type != "recruiter":
        return None
    fixed = _RECRUITER_KIND.get(event.event_type)
    if fixed is not None:
        return fixed
    if event.event_type == "application_outcome_changed":
        if (event.to_outcome or "").lower() == "rejected":
            return "reject"
        if (event.to_outcome or "").lower() in {"hired"}:
            return "advance"
    if event.event_type == "pipeline_stage_changed":
        if (event.to_stage or "").lower() in {
            "technical_interview",
            "interview",
            "offer",
            "hired",
        }:
            return "advance"
    return None


def _scores_at_event(app: CandidateApplication) -> dict[str, float]:
    """Use the scores currently cached on the application. We do NOT
    backfill — if the application was unscored at the time of the
    event, we operate on whatever is on the row now (best effort).
    """
    out: dict[str, float] = {}
    if app.role_fit_score_cache_100 is not None:
        out["role_fit_score"] = float(app.role_fit_score_cache_100)
    elif (
        isinstance(app.cv_match_details, dict)
        and app.cv_match_details.get("role_fit_score") is not None
    ):
        out["role_fit_score"] = float(app.cv_match_details["role_fit_score"])
    if app.pre_screen_score_100 is not None:
        out["pre_screen_score"] = float(app.pre_screen_score_100)
    if app.taali_score_cache_100 is not None:
        out["taali_score"] = float(app.taali_score_cache_100)
    if app.assessment_score_cache_100 is not None:
        out["assessment_score"] = float(app.assessment_score_cache_100)
    return out


def disagreement_for_manual_event(
    db: Session, *, event: CandidateApplicationEvent
) -> Disagreement | None:
    """Run the current policy retroactively against ``event``.

    Returns ``None`` if the event isn't a meaningful recruiter action;
    otherwise a ``Disagreement`` with one of the four patterns or
    ``pattern='agreement'``.
    """
    recruiter_kind = _classify_event(event)
    if recruiter_kind is None:
        return None

    app = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.id == event.application_id)
        .one_or_none()
    )
    if app is None:
        return None
    role = (
        db.query(Role)
        .filter(
            Role.id == app.role_id,
            Role.organization_id == app.organization_id,
        )
        .one_or_none()
    )
    if role is None:
        return None

    inputs = DecisionInputs(
        application_id=int(app.id),
        role_id=int(role.id),
        organization_id=int(app.organization_id),
        scores=_scores_at_event(app),
        graph_priors={},
        intent={},
        flags={
            # Retroactive: we don't know the assessment_completed
            # state at the moment of the event, so use the current
            # value (good enough for retune signal).
            "has_pending_assessment": False,
            "no_pending_assessment": True,
            "assessment_completed": app.assessment_score_cache_100 is not None,
            "must_have_blocked": False,
        },
        manual_actions=[],  # disable skip — we WANT the policy to opine
    )
    verdict = evaluate(inputs, db=db)

    pattern = _diagnose(
        recruiter_kind=recruiter_kind,
        policy_decision=verdict.decision_type,
        decision_point=verdict.decision_point,
    )
    return Disagreement(
        pattern=pattern,
        decision_point=verdict.decision_point,
        policy_decision_type=verdict.decision_type,
        recruiter_kind=recruiter_kind,
    )


# ---------------------------------------------------------------------------
# Pattern table
# ---------------------------------------------------------------------------


_REJECT_VERDICTS = {
    "queue_reject_decision",
    "queue_skip_assessment_reject_decision",
    "auto_reject",
}
_SEND_VERDICTS = {"queue_send_assessment"}
_ADVANCE_VERDICTS = {"queue_advance_decision"}


def _diagnose(
    *,
    recruiter_kind: str,
    policy_decision: str,
    decision_point: str | None,
) -> str:
    if recruiter_kind == "send" and policy_decision in _REJECT_VERDICTS:
        return "manual-send-on-would-reject"
    if recruiter_kind == "reject" and policy_decision in _SEND_VERDICTS:
        return "manual-reject-on-would-send"
    if recruiter_kind == "advance" and policy_decision in _REJECT_VERDICTS:
        # Distinguish advance-vs-reject post-assessment from pre.
        if decision_point in {"reject", "advance_to_interview"}:
            return "manual-advance-on-would-reject-post-assessment"
        return "manual-advance-on-would-reject-post-assessment"
    if recruiter_kind == "reject" and policy_decision in _ADVANCE_VERDICTS:
        return "manual-reject-on-would-advance"
    return "agreement"


__all__ = ["Disagreement", "disagreement_for_manual_event"]
