"""Role-local, recruiter-authorized re-screening for related roles.

Related roles own their candidate membership and score lifecycle in
``SisterRoleEvaluation``.  The linked ATS role/application is optional
transport evidence only: it may be locked when present so linkage cannot move
mid-transition, but its lifecycle never grants or revokes local membership.

This module is the single mutation boundary used by Agent Chat's constraint
re-screen and old-engine re-score tools.  It deliberately separates the
transactional reset from broker publication:

1. lock Organization -> ordered Roles -> Candidates -> Applications ->
   Evaluations -> Decisions (and, only when explicitly requested, Assessments),
2. archive/reset only the acting role's live memberships,
3. discard only that role's actionable decisions,
4. commit the complete role-local transition, then
5. publish the durable pending evaluation ids.

No provider is called here.  The scoring worker retains the final live-role,
pause, budget, and generation checks immediately before any paid work.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.assessment import Assessment
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_UNSCORABLE,
    SisterRoleEvaluation,
)
from .related_role_rescreen_support import (
    RelatedRoleRescreenUnavailableError,
    is_related_role as _is_related_role,
    locked_role_snapshot as _locked_role_snapshot,
    membership_identities as _membership_identities,
    score_is_outdated as _score_is_outdated,
)
from .sister_role_evaluation_lifecycle import reset_evaluation_for_rescore

logger = logging.getLogger("taali.related_role_rescreen")

_ACTIONABLE_DECISION_STATUSES = (
    "pending",
    "processing",
    "reverted_for_feedback",
)


@dataclass(frozen=True)
class RelatedRoleRescreenResult:
    """Truthful persisted and publication counts for one re-screen request."""

    role_id: int
    requested_count: int | None
    matched_count: int
    reset_count: int
    queued_count: int
    waiting_count: int
    unscorable_count: int
    skipped_resolved_count: int
    skipped_current_count: int
    missing_membership_count: int
    decisions_invalidated: int
    assessments_voided: int
    evaluation_ids: tuple[int, ...]

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["evaluation_ids"] = list(self.evaluation_ids)
        return payload


def _empty_result(
    *,
    role_id: int,
    requested_count: int | None,
    missing_membership_count: int = 0,
) -> RelatedRoleRescreenResult:
    return RelatedRoleRescreenResult(
        role_id=int(role_id),
        requested_count=requested_count,
        matched_count=0,
        reset_count=0,
        queued_count=0,
        waiting_count=0,
        unscorable_count=0,
        skipped_resolved_count=0,
        skipped_current_count=0,
        missing_membership_count=int(missing_membership_count),
        decisions_invalidated=0,
        assessments_voided=0,
        evaluation_ids=(),
    )


def rescreen_related_role_candidates(
    db: Session,
    role: Role,
    *,
    reason: str,
    application_ids: list[int] | None = None,
    only_outdated: bool = False,
    void_active_assessments: bool = False,
    require_all_memberships: bool = False,
) -> RelatedRoleRescreenResult:
    """Reset and publish a bounded set of this related role's memberships.

    ``None`` selects the full live roster; an explicit empty list selects
    nobody. ``only_outdated`` is used by the engine-migration re-score tool so
    a score that completed between preview and confirmation is not reset.

    Score refresh alone normally preserves an already-started assessment.
    Membership-restart callers may explicitly set ``void_active_assessments``;
    those rows are then voided under the same role-local lock boundary instead
    of silently carrying an attempt into a new candidate lifecycle.
    """

    if not _is_related_role(role):
        raise ValueError("Role is not a related role")
    normalized_ids = (
        sorted({int(value) for value in application_ids})
        if application_ids is not None
        else None
    )
    requested_count = len(normalized_ids) if normalized_ids is not None else None
    if normalized_ids == []:
        return _empty_result(
            role_id=int(role.id),
            requested_count=0,
        )

    locked_role = _locked_role_snapshot(db, role=role)
    identities = _membership_identities(
        db,
        role_id=int(locked_role.id),
        organization_id=int(locked_role.organization_id),
        application_ids=normalized_ids,
    )
    matched_count = len(identities)
    missing_membership_count = (
        max(int(requested_count or 0) - matched_count, 0)
        if requested_count is not None
        else 0
    )
    if require_all_memberships and missing_membership_count:
        # Autonomous batches validate before entering this service, then repeat
        # the all-or-nothing check under the live Organization/Role locks.  If a
        # membership disappeared between those boundaries, do not reset or
        # publish the still-valid subset.
        db.rollback()
        raise RelatedRoleRescreenUnavailableError(
            "One or more candidates are no longer in this related role. "
            "No candidates were re-screened."
        )
    if not identities:
        # Release the Organization/Role locks. There is no persisted transition
        # and therefore nothing that may be published after this boundary.
        db.rollback()
        return _empty_result(
            role_id=int(locked_role.id),
            requested_count=requested_count,
            missing_membership_count=missing_membership_count,
        )

    candidate_ids = sorted({item.candidate_id for item in identities})
    candidates = (
        db.query(Candidate)
        .filter(
            Candidate.id.in_(candidate_ids),
            Candidate.organization_id == int(locked_role.organization_id),
        )
        .order_by(Candidate.id.asc())
        .with_for_update(of=Candidate)
        .populate_existing()
        .all()
    )
    candidates_by_id = {int(candidate.id): candidate for candidate in candidates}

    all_application_ids = sorted(
        {
            value
            for item in identities
            for value in (
                item.source_application_id,
                item.ats_application_id,
            )
            if value is not None
        }
    )
    applications = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id.in_(all_application_ids),
            CandidateApplication.organization_id
            == int(locked_role.organization_id),
        )
        .order_by(CandidateApplication.id.asc())
        .with_for_update(of=CandidateApplication)
        .populate_existing()
        .all()
    )
    applications_by_id = {
        int(application.id): application for application in applications
    }

    evaluation_ids = [item.evaluation_id for item in identities]
    evaluations = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.id.in_(evaluation_ids),
            SisterRoleEvaluation.organization_id
            == int(locked_role.organization_id),
            SisterRoleEvaluation.role_id == int(locked_role.id),
            SisterRoleEvaluation.deleted_at.is_(None),
        )
        .order_by(SisterRoleEvaluation.id.asc())
        .with_for_update(of=SisterRoleEvaluation)
        .populate_existing()
        .all()
    )
    identity_by_evaluation_id = {
        item.evaluation_id: item for item in identities
    }

    dispatchable_ids: list[int] = []
    reset_ids: list[int] = []
    reset_application_ids: list[int] = []
    reset_candidate_ids: list[int] = []
    unscorable_count = 0
    skipped_resolved_count = 0
    skipped_current_count = 0
    for evaluation in evaluations:
        identity = identity_by_evaluation_id[int(evaluation.id)]
        if (
            str(evaluation.application_outcome or "open").strip().lower()
            != "open"
            or str(evaluation.pipeline_stage or "applied").strip().lower()
            == "advanced"
        ):
            skipped_resolved_count += 1
            continue
        if only_outdated and not _score_is_outdated(evaluation):
            skipped_current_count += 1
            continue

        application = applications_by_id.get(identity.source_application_id)
        candidate = candidates_by_id.get(identity.candidate_id)
        if (
            application is None
            or int(application.candidate_id) != identity.candidate_id
        ):
            # A source application FK should make this impossible. Fail closed
            # rather than consulting the optional ATS transport as a substitute.
            continue
        candidate_available = bool(
            candidate is not None and candidate.deleted_at is None
        )
        cv_text = str(application.cv_text or "").strip()
        if not cv_text and candidate_available:
            cv_text = str(candidate.cv_text or "").strip()
        dispatchable = reset_evaluation_for_rescore(
            evaluation,
            role_id=int(locked_role.id),
            application_id=int(identity.source_application_id),
            cv_text=cv_text if candidate_available else "",
            job_spec=str(locked_role.job_spec_text or ""),
        )
        if not candidate_available:
            evaluation.status = SISTER_EVAL_UNSCORABLE
            evaluation.last_error_code = "candidate_unavailable"
            evaluation.error_message = "Candidate is unavailable"
            dispatchable = False
        reset_ids.append(int(evaluation.id))
        reset_application_ids.append(int(identity.source_application_id))
        reset_candidate_ids.append(int(identity.candidate_id))
        if dispatchable:
            dispatchable_ids.append(int(evaluation.id))
        else:
            unscorable_count += 1

    decisions_invalidated = 0
    if reset_application_ids:
        decisions = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.organization_id
                == int(locked_role.organization_id),
                AgentDecision.role_id == int(locked_role.id),
                AgentDecision.application_id.in_(reset_application_ids),
                AgentDecision.status.in_(_ACTIONABLE_DECISION_STATUSES),
            )
            .order_by(AgentDecision.id.asc())
            .with_for_update(of=AgentDecision)
            .populate_existing()
            .all()
        )
        now = datetime.now(timezone.utc)
        resolution_note = (
            f"superseded: {reason}; role-local score refresh required"
        )[:500]
        for decision in decisions:
            decision.status = "discarded"
            decision.resolved_at = now
            decision.resolution_note = resolution_note
            decisions_invalidated += 1

    assessments_voided = 0
    if void_active_assessments and reset_candidate_ids:
        assessments = (
            db.query(Assessment)
            .filter(
                Assessment.organization_id == int(locked_role.organization_id),
                Assessment.role_id == int(locked_role.id),
                # Assessment application_id is evidence/transport metadata.
                # Restart the logical membership by its tenant, role and
                # candidate identity so a transport-linked attempt cannot
                # survive the reset.
                Assessment.candidate_id.in_(sorted(set(reset_candidate_ids))),
                Assessment.is_voided.is_(False),
            )
            .order_by(Assessment.id.asc())
            .with_for_update(of=Assessment)
            .populate_existing()
            .all()
        )
        now = datetime.now(timezone.utc)
        for assessment in assessments:
            assessment.is_voided = True
            assessment.voided_at = now
            assessment.void_reason = (
                f"Superseded by a new role-local evaluation: {reason}"
            )[:1000]
            assessments_voided += 1

    db.flush()
    # This is the publication boundary. A worker can never observe a pending
    # delivery until the archived score, discarded decisions, and optional
    # assessment invalidation are all durable.
    db.commit()

    queued_count = 0
    waiting_count = 0
    from ..tasks.sister_role_tasks import dispatch_sister_evaluation

    for evaluation_id in dispatchable_ids:
        try:
            dispatch = dispatch_sister_evaluation(
                db,
                evaluation_id=int(evaluation_id),
            )
            if dispatch.get("status") == "queued":
                queued_count += 1
            else:
                waiting_count += 1
        except Exception:  # pragma: no cover - durable pending row is recoverable
            db.rollback()
            waiting_count += 1
            logger.exception(
                "related role re-screen dispatch failed role_id=%s evaluation_id=%s",
                locked_role.id,
                evaluation_id,
            )

    return RelatedRoleRescreenResult(
        role_id=int(locked_role.id),
        requested_count=requested_count,
        matched_count=matched_count,
        reset_count=len(reset_ids),
        queued_count=queued_count,
        waiting_count=waiting_count,
        unscorable_count=unscorable_count,
        skipped_resolved_count=skipped_resolved_count,
        skipped_current_count=skipped_current_count,
        missing_membership_count=missing_membership_count,
        decisions_invalidated=decisions_invalidated,
        assessments_voided=assessments_voided,
        evaluation_ids=tuple(reset_ids),
    )


__all__ = [
    "RelatedRoleRescreenResult",
    "RelatedRoleRescreenUnavailableError",
    "rescreen_related_role_candidates",
]
