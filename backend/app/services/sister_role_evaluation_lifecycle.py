"""State transitions for one persisted related-role evaluation."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_PENDING,
    SISTER_EVAL_RETRY_WAIT,
    SISTER_EVAL_STALE_HELD,
    SISTER_EVAL_UNSCORABLE,
    SisterRoleEvaluation,
)


def archive_evaluation_result(evaluation: SisterRoleEvaluation) -> None:
    if (
        evaluation.scored_at is None
        and evaluation.role_fit_score is None
        and not evaluation.details
    ):
        return
    history = list(evaluation.history or [])
    history.append(
        {
            "status": evaluation.status,
            "role_fit_score": evaluation.role_fit_score,
            "summary": evaluation.summary,
            "spec_fingerprint": evaluation.spec_fingerprint,
            "cv_fingerprint": evaluation.cv_fingerprint,
            "model_version": evaluation.model_version,
            "prompt_version": evaluation.prompt_version,
            "trace_id": evaluation.trace_id,
            "cache_hit": bool(evaluation.cache_hit),
            "scored_at": (
                evaluation.scored_at.isoformat()
                if evaluation.scored_at
                else None
            ),
        }
    )
    evaluation.history = history[-20:]


def reset_evaluation_for_rescore(
    evaluation: SisterRoleEvaluation,
    *,
    role_id: int,
    application_id: int,
    cv_text: str,
    job_spec: str,
    hold_for_explicit_release: bool = False,
) -> bool:
    """Invalidate exactly one role score; return whether it can be dispatched.

    Passive provider syncs set ``hold_for_explicit_release`` so changed inputs
    remain visible without silently authorising another paid model call. An
    explicit recruiter re-evaluation uses the default and creates ordinary
    pending work.
    """

    if (
        int(evaluation.role_id) != int(role_id)
        or int(evaluation.source_application_id) != int(application_id)
    ):
        raise ValueError("Related evaluation does not match role/application")
    cv_text = str(cv_text or "").strip()
    job_spec = str(job_spec or "").strip()
    archive_evaluation_result(evaluation)
    evaluation.spec_fingerprint = _text_fingerprint(job_spec)
    evaluation.cv_fingerprint = _text_fingerprint(cv_text) if cv_text else None
    evaluation.role_fit_score = None
    evaluation.summary = None
    evaluation.details = None
    evaluation.cache_hit = False
    evaluation.model_version = None
    evaluation.prompt_version = None
    evaluation.trace_id = None
    evaluation.attempts = 0
    evaluation.next_attempt_at = None
    evaluation.dispatch_attempted_at = None
    evaluation.queued_at = datetime.now(timezone.utc)
    evaluation.started_at = None
    evaluation.scored_at = None
    if not cv_text or not job_spec:
        evaluation.status = SISTER_EVAL_UNSCORABLE
        evaluation.last_error_code = (
            "missing_cv_text" if not cv_text else "missing_job_specification"
        )
        evaluation.error_message = (
            "No CV text available" if not cv_text else "No job specification available"
        )
        return False
    if hold_for_explicit_release:
        evaluation.status = SISTER_EVAL_STALE_HELD
        evaluation.last_error_code = "shared_inputs_changed"
        evaluation.error_message = (
            "Candidate inputs changed; recruiter re-evaluation is required"
        )
        return False
    evaluation.status = SISTER_EVAL_PENDING
    evaluation.last_error_code = None
    evaluation.error_message = None
    return True


def release_sister_role_score_holds(
    *,
    organization_id: int,
    role_id: int,
) -> int:
    """Release durable retry/stale holds at an explicit authority boundary.

    Celery task payloads deliberately remain the historical one-argument shape,
    so a newly deployed API can still hand work to an older worker during a
    rolling release.  The durable row transition is the cross-version signal:
    old and new workers both already dispatch ``pending`` evaluations.

    This owns a short transaction and takes the canonical Organization -> Role
    -> evaluation lock order.  Tenant/role filters make the transition safe for
    direct callers, while the conditional held-status predicate makes repeated
    activation/resume requests idempotent.
    """

    from ..platform.database import SessionLocal

    with SessionLocal() as db:
        organization = (
            db.query(Organization.id)
            .filter(Organization.id == int(organization_id))
            .with_for_update(of=Organization)
            .scalar()
        )
        if organization is None:
            db.rollback()
            return 0
        role = (
            db.query(Role.id)
            .filter(
                Role.id == int(role_id),
                Role.organization_id == int(organization_id),
                Role.role_kind == ROLE_KIND_SISTER,
                Role.deleted_at.is_(None),
            )
            .with_for_update(of=Role)
            .scalar()
        )
        if role is None:
            db.rollback()
            return 0
        evaluations = (
            db.query(SisterRoleEvaluation)
            .filter(
                SisterRoleEvaluation.organization_id == int(organization_id),
                SisterRoleEvaluation.role_id == int(role_id),
                SisterRoleEvaluation.status.in_(
                    (SISTER_EVAL_RETRY_WAIT, SISTER_EVAL_STALE_HELD)
                ),
            )
            .order_by(SisterRoleEvaluation.id.asc())
            .with_for_update(of=SisterRoleEvaluation)
            .all()
        )
        now = datetime.now(timezone.utc)
        for evaluation in evaluations:
            evaluation.status = SISTER_EVAL_PENDING
            evaluation.next_attempt_at = None
            evaluation.dispatch_attempted_at = None
            evaluation.started_at = None
            evaluation.queued_at = now
        db.commit()
        return len(evaluations)


def reset_related_evaluations_for_application(
    db: Session,
    application: CandidateApplication,
    *,
    reason: str,
    queue_for_rescore: bool = False,
) -> list[int]:
    """Invalidate every live related-role score over one shared application.

    The caller owns commit timing. Passive input refreshes use the default and
    move scoreable rows to ``stale_held``: the old decision stays visible and
    approval-blocked until the recruiter selects Re-evaluate. Explicit CV or
    re-score flows opt into ``queue_for_rescore`` and may create pending paid
    work, but this function itself never publishes to the broker.

    Locking starts at the shared Candidate/Application and then the role-local
    evaluations.  That is the same Candidate -> Application -> Evaluation suffix
    used by related-role scoring/decision cycles.  Related Role rows are used
    only as tenant/ownership filters and are not locked after the Application,
    avoiding an inversion of the platform's Organization -> Role -> Application
    order.
    """

    application_id = getattr(application, "id", None)
    organization_id = getattr(application, "organization_id", None)
    owner_role_id = getattr(application, "role_id", None)
    candidate_id = getattr(application, "candidate_id", None)
    if (
        application_id is None
        or organization_id is None
        or owner_role_id is None
        or candidate_id is None
        or getattr(application, "deleted_at", None) is not None
    ):
        return []

    from .sister_role_service import (
        application_cv_text,
        source_application_is_globally_advanced,
        source_application_is_globally_closed,
    )

    # Respect caller-owned terminal changes before consulting the committed row.
    if source_application_is_globally_closed(
        application
    ) or source_application_is_globally_advanced(application):
        return []

    # Lock scalar projections in Candidate -> Application order so concurrent
    # scoring cannot cross the reset boundary, without populate_existing()
    # overwriting dirty Workable/CV fields on caller-owned ORM objects.
    with db.no_autoflush:
        live_candidate_id = (
            db.query(Candidate.id)
            .filter(
                Candidate.id == int(candidate_id),
                Candidate.organization_id == int(organization_id),
                Candidate.deleted_at.is_(None),
            )
            .with_for_update(of=Candidate)
            .scalar()
        )
        if live_candidate_id is None:
            return []
        live_state = (
            db.query(
                CandidateApplication.application_outcome,
                CandidateApplication.pipeline_stage,
                CandidateApplication.workable_disqualified,
            )
            .filter(
                CandidateApplication.id == int(application_id),
                CandidateApplication.organization_id == int(organization_id),
                CandidateApplication.role_id == int(owner_role_id),
                CandidateApplication.deleted_at.is_(None),
            )
            .with_for_update(of=CandidateApplication)
            .one_or_none()
        )
    if live_state is None:
        return []
    live_outcome, live_stage, live_disqualified = live_state
    if (
        str(live_outcome or "open") != "open"
        or bool(live_disqualified)
        or str(live_stage or "").strip().lower() == "advanced"
    ):
        return []

    # Lock only the evaluation rows after the shared application.  Role columns
    # are a fresh, tenant-scoped prompt snapshot; the scoring worker rechecks
    # live Role authority/specification before spending and again before save.
    with db.no_autoflush:
        rows = (
            db.query(SisterRoleEvaluation, Role.job_spec_text)
            .join(Role, Role.id == SisterRoleEvaluation.role_id)
            .filter(
                SisterRoleEvaluation.organization_id == int(organization_id),
                SisterRoleEvaluation.source_application_id == int(application_id),
                SisterRoleEvaluation.pipeline_stage != "advanced",
                Role.organization_id == int(organization_id),
                Role.role_kind == ROLE_KIND_SISTER,
                Role.ats_owner_role_id == int(owner_role_id),
                Role.deleted_at.is_(None),
            )
            .order_by(SisterRoleEvaluation.id.asc())
            .with_for_update(of=SisterRoleEvaluation)
            .populate_existing()
            .all()
        )
    if not rows:
        return []

    cv_text = application_cv_text(application)
    evaluation_ids: list[int] = []
    related_role_ids: set[int] = set()
    for evaluation, job_spec_text in rows:
        reset_evaluation_for_rescore(
            evaluation,
            role_id=int(evaluation.role_id),
            application_id=int(application_id),
            cv_text=cv_text,
            job_spec=str(job_spec_text or ""),
            hold_for_explicit_release=not queue_for_rescore,
        )
        evaluation_ids.append(int(evaluation.id))
        related_role_ids.add(int(evaluation.role_id))

    # Materialize the generation reset while Application/Evaluation locks are
    # held.  An old in-flight scorer then necessarily observes a changed lease
    # and cannot persist its provider output.
    db.flush()

    now = datetime.now(timezone.utc)
    resolution_note = (
        f"superseded: {reason}; related-role score refresh required"
    )[:500]
    decision_scope = db.query(AgentDecision).filter(
        AgentDecision.organization_id == int(organization_id),
        AgentDecision.application_id == int(application_id),
        AgentDecision.role_id.in_(sorted(related_role_ids)),
    )
    if queue_for_rescore:
        # An explicit replacement is already authorised. Discard every old
        # actionable/accepted generation, including a processing receipt whose
        # worker has not yet acquired the Application lock. If that worker had
        # crossed the execution lock first, this reset would wait and observe
        # its terminal application transition instead of reaching this update.
        decision_scope.filter(
            AgentDecision.status.in_(
                ("pending", "processing", "reverted_for_feedback")
            )
        ).update(
            {
                AgentDecision.status: "discarded",
                AgentDecision.resolved_at: now,
                AgentDecision.resolution_note: resolution_note,
            },
            synchronize_session="fetch",
        )
    else:
        # Keep pending/taught cards visible so they expose the recruiter-owned
        # Re-evaluate action. A processing receipt is moved back to the same
        # visible lane; its already-published worker is harmless because the
        # held evaluation fails the locked approval freshness gate.
        decision_scope.filter(AgentDecision.status == "processing").update(
            {AgentDecision.status: "pending"},
            synchronize_session="fetch",
        )
    db.flush()
    return evaluation_ids


def _text_fingerprint(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


__all__ = [
    "archive_evaluation_result",
    "release_sister_role_score_holds",
    "reset_evaluation_for_rescore",
    "reset_related_evaluations_for_application",
]
