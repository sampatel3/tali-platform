"""Validity-scoped roster queries for related-role projections."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session, aliased, joinedload

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, ROLE_KIND_STANDARD, Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_DONE,
    SisterRoleEvaluation,
)


RELATED_ROSTER_EXCLUSION_CODE = "source_application_outside_owner_roster"


def related_source_application_is_live(
    role: Role | None,
    application: CandidateApplication | None,
) -> bool:
    """Return whether a delayed worker still owns this related-role row.

    The application, candidate, and canonical ATS owner are independently
    mutable after an evaluation is queued.  This in-memory check mirrors the
    SQL roster predicates below so workers can revoke stale deliveries at the
    final pre-provider boundary.
    """

    if (
        role is None
        or application is None
        or str(role.role_kind or "") != ROLE_KIND_SISTER
        or role.deleted_at is not None
        or not role.ats_owner_role_id
        or application.organization_id != role.organization_id
        or application.role_id != role.ats_owner_role_id
        or application.deleted_at is not None
    ):
        return False

    candidate = application.candidate
    owner_role = application.role
    return bool(
        candidate is not None
        and candidate.organization_id == role.organization_id
        and candidate.deleted_at is None
        and owner_role is not None
        and owner_role.id == role.ats_owner_role_id
        and owner_role.organization_id == role.organization_id
        and str(owner_role.role_kind or ROLE_KIND_STANDARD) == ROLE_KIND_STANDARD
        and owner_role.ats_owner_role_id is None
        and owner_role.deleted_at is None
    )


def active_source_applications_for_related_role(
    db: Session,
    role: Role,
) -> list[CandidateApplication]:
    """Return only live, ownership-valid applications in a related roster."""

    return (
        db.query(CandidateApplication)
        .options(joinedload(CandidateApplication.candidate))
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.organization_id == role.organization_id,
            CandidateApplication.role_id == role.ats_owner_role_id,
            CandidateApplication.deleted_at.is_(None),
            Candidate.organization_id == role.organization_id,
            Candidate.deleted_at.is_(None),
            Role.organization_id == role.organization_id,
            Role.role_kind == ROLE_KIND_STANDARD,
            Role.ats_owner_role_id.is_(None),
            Role.deleted_at.is_(None),
        )
        .all()
    )


def _empty_related_pipeline_counts() -> dict[str, int]:
    return {
        "applied": 0,
        "scored": 0,
        "invited": 0,
        "in_assessment": 0,
        "completed": 0,
        "advanced": 0,
        "rejected": 0,
        "not_yet_decided": 0,
        "invited_delivered": 0,
        "invited_opened": 0,
    }


def related_role_pipeline_counts_bulk(
    db: Session,
    role_ids: list[int],
) -> dict[int, dict[str, int]]:
    """Load valid independent related-role funnels without per-role queries."""

    role_ids = [int(role_id) for role_id in role_ids]
    counts_by_role = {role_id: _empty_related_pipeline_counts() for role_id in role_ids}
    if not role_ids:
        return counts_by_role

    related_role = aliased(Role, name="related_role")
    owner_role = aliased(Role, name="related_role_owner")
    rows = (
        db.query(
            SisterRoleEvaluation.role_id,
            SisterRoleEvaluation.pipeline_stage,
            SisterRoleEvaluation.status,
            CandidateApplication.application_outcome,
            CandidateApplication.workable_disqualified,
            func.count(SisterRoleEvaluation.id),
        )
        .join(related_role, related_role.id == SisterRoleEvaluation.role_id)
        .join(owner_role, owner_role.id == related_role.ats_owner_role_id)
        .join(
            CandidateApplication,
            CandidateApplication.id == SisterRoleEvaluation.source_application_id,
        )
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            SisterRoleEvaluation.role_id.in_(role_ids),
            related_role.role_kind == ROLE_KIND_SISTER,
            related_role.deleted_at.is_(None),
            owner_role.deleted_at.is_(None),
            owner_role.organization_id == related_role.organization_id,
            owner_role.role_kind == ROLE_KIND_STANDARD,
            owner_role.ats_owner_role_id.is_(None),
            CandidateApplication.role_id == owner_role.id,
            CandidateApplication.organization_id == related_role.organization_id,
            CandidateApplication.deleted_at.is_(None),
            Candidate.organization_id == related_role.organization_id,
            Candidate.deleted_at.is_(None),
            SisterRoleEvaluation.organization_id == related_role.organization_id,
        )
        .group_by(
            SisterRoleEvaluation.role_id,
            SisterRoleEvaluation.pipeline_stage,
            SisterRoleEvaluation.status,
            CandidateApplication.application_outcome,
            CandidateApplication.workable_disqualified,
        )
        .all()
    )
    for role_id, stage, score_status, outcome, disqualified, total in rows:
        counts = counts_by_role[int(role_id)]
        total = int(total or 0)
        canonical_outcome = str(outcome or "open").strip().lower()
        if canonical_outcome == "rejected" or bool(disqualified):
            counts["rejected"] += total
            continue
        if canonical_outcome != "open":
            continue
        local_stage = str(stage or "applied")
        if local_stage == "applied" and score_status == SISTER_EVAL_DONE:
            counts["scored"] += total
        elif local_stage == "in_assessment":
            counts["invited"] += total
            counts["in_assessment"] += total
            counts["invited_delivered"] += total
            counts["invited_opened"] += total
        elif local_stage == "review":
            counts["completed"] += total
        elif local_stage in counts:
            counts[local_stage] += total
        else:
            counts["applied"] += total
    return counts_by_role
