"""Durable post-commit related-role fan-out for ATS applications.

Workable and Bullhorn imports run in a larger transaction. Publishing scoring
from that transaction is unsafe: a fast worker can observe no committed row,
and a broker outage can lose the only kick. The application-ingest outbox calls
this module after commit; the evaluation row is the idempotent recovery receipt.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_ERROR,
    SISTER_EVAL_PENDING,
    SISTER_EVAL_RETRY_WAIT,
    SISTER_EVAL_RUNNING,
    SisterRoleEvaluation,
)


def _live_sister_roles(
    db: Session, application: CandidateApplication
) -> list[Role]:
    return (
        db.query(Role)
        .filter(
            Role.organization_id == int(application.organization_id),
            Role.role_kind == ROLE_KIND_SISTER,
            Role.ats_owner_role_id == int(application.role_id),
            Role.deleted_at.is_(None),
        )
        .order_by(Role.id.asc())
        .all()
    )


def related_role_work_pending(
    db: Session, application: CandidateApplication
) -> bool:
    """Return whether a post-commit fan-out has durable work to perform."""

    sisters = _live_sister_roles(db, application)
    if not sisters:
        return False

    from .sister_role_service import application_cv_text, text_fingerprint

    evaluation_by_role = {
        int(row.role_id): row
        for row in db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.source_application_id == int(application.id),
            SisterRoleEvaluation.role_id.in_([int(role.id) for role in sisters]),
        )
        .all()
    }
    cv_text = application_cv_text(application)
    cv_fingerprint = text_fingerprint(cv_text) if cv_text else None
    for sister in sisters:
        evaluation = evaluation_by_role.get(int(sister.id))
        if evaluation is None or evaluation.status in {
            SISTER_EVAL_PENDING,
            SISTER_EVAL_RETRY_WAIT,
            SISTER_EVAL_RUNNING,
        }:
            return True
        # Rows written before the recovery rail used ``error`` for transient
        # broker/provider failures. A missing safe code identifies that legacy
        # state so it is not silently stranded after deployment.
        if (
            evaluation.status == SISTER_EVAL_ERROR
            and evaluation.last_error_code is None
        ):
            return True
        if evaluation.cv_fingerprint != cv_fingerprint:
            return True
        if evaluation.spec_fingerprint != text_fingerprint(sister.job_spec_text):
            return True
    return False


def dispatch_related_role_work(
    db: Session, application: CandidateApplication
) -> dict[str, int]:
    """Persist current evaluations, then publish every pending row.

    The commit deliberately precedes broker publication.  Retrying after a
    partial publish is safe: scoring workers lock the evaluation and only run
    rows still in ``pending`` state.
    """

    sisters = _live_sister_roles(db, application)
    if not sisters:
        return {"evaluations": 0, "dispatched": 0}

    from .sister_role_service import ensure_application_sister_evaluations

    ensure_application_sister_evaluations(
        db,
        application,
        sister_roles=sisters,
    )
    db.commit()

    recoverable_ids = [
        int(row_id)
        for (row_id,) in db.query(SisterRoleEvaluation.id)
        .filter(
            SisterRoleEvaluation.source_application_id == int(application.id),
            SisterRoleEvaluation.role_id.in_([int(role.id) for role in sisters]),
            SisterRoleEvaluation.status.in_(
                (SISTER_EVAL_PENDING, SISTER_EVAL_RETRY_WAIT, SISTER_EVAL_RUNNING)
            ),
        )
        .order_by(SisterRoleEvaluation.id.asc())
        .all()
    ]
    results: list[dict] = []
    if recoverable_ids:
        from ..tasks.sister_role_tasks import dispatch_sister_evaluation

        for evaluation_id in recoverable_ids:
            results.append(
                dispatch_sister_evaluation(db, evaluation_id=evaluation_id)
            )
    return {
        "evaluations": len(sisters),
        "dispatched": sum(item["status"] == "queued" for item in results),
    }


__all__ = ["dispatch_related_role_work", "related_role_work_pending"]
