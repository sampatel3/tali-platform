"""Durable post-commit scoring recovery for explicit related-role members.

Workable and Bullhorn imports run in a larger transaction. Publishing scoring
from that transaction is unsafe: a fast worker can observe no committed row,
and a broker outage can lose the only kick. The application-ingest outbox calls
this module after commit only for memberships that already exist; it never
enrols a new ATS applicant into a related role.
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
    """Return whether an explicit membership has durable transport work.

    An ATS resync is not scoring authority. Input changes are handled at their
    explicit lifecycle boundary; this recovery check only notices work that was
    already pending/running/retryable before the resync.
    """

    sisters = _live_sister_roles(db, application)
    if not sisters:
        return False

    evaluation_by_role = {
        int(row.role_id): row
        for row in db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.source_application_id == int(application.id),
            SisterRoleEvaluation.role_id.in_([int(role.id) for role in sisters]),
            SisterRoleEvaluation.deleted_at.is_(None),
        )
        .all()
    }
    for sister in sisters:
        evaluation = evaluation_by_role.get(int(sister.id))
        if evaluation is None:
            # Missing membership is deliberate. A newly imported owner
            # application must not enrol itself into every related role.
            continue
        if evaluation.status in {
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
    return False


def dispatch_related_role_work(
    db: Session, application: CandidateApplication
) -> dict[str, int]:
    """Recover already-authorised pending work without refreshing inputs.

    Retrying after a partial publish is safe: scoring workers lock the
    evaluation and only run rows still in a recoverable state. This path never
    clears a score or turns an ATS/full-resync observation into paid work.
    """

    sisters = _live_sister_roles(db, application)
    if not sisters:
        return {"evaluations": 0, "dispatched": 0}

    member_role_ids = {
        int(role_id)
        for (role_id,) in db.query(SisterRoleEvaluation.role_id)
        .filter(
            SisterRoleEvaluation.organization_id == int(application.organization_id),
            SisterRoleEvaluation.source_application_id == int(application.id),
            SisterRoleEvaluation.deleted_at.is_(None),
            SisterRoleEvaluation.role_id.in_([int(role.id) for role in sisters]),
        )
        .all()
    }
    sisters = [role for role in sisters if int(role.id) in member_role_ids]
    if not sisters:
        return {"evaluations": 0, "dispatched": 0}

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
        "evaluations": len(member_role_ids),
        "dispatched": sum(item["status"] == "queued" for item in results),
    }


__all__ = ["dispatch_related_role_work", "related_role_work_pending"]
