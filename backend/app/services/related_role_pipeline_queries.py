"""Query expressions for independent related-role pipeline state."""

from __future__ import annotations

from sqlalchemy import and_, asc, case, desc, func
from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_STANDARD, Role
from ..models.sister_role_evaluation import SisterRoleEvaluation


def stage_column(*, related: bool):
    """Return the local related stage or the canonical application stage."""

    if related:
        return case(
            (
                func.lower(
                    func.trim(
                        func.coalesce(CandidateApplication.pipeline_stage, "")
                    )
                )
                == "advanced",
                "advanced",
            ),
            else_=func.coalesce(SisterRoleEvaluation.pipeline_stage, "applied"),
        )
    return CandidateApplication.pipeline_stage


def valid_source_scope(*, organization_id: int, owner_role_id: int):
    """Require a live candidate and canonical owner for a related roster."""

    return and_(
        CandidateApplication.organization_id == int(organization_id),
        CandidateApplication.role_id == int(owner_role_id),
        CandidateApplication.deleted_at.is_(None),
        CandidateApplication.candidate.has(and_(
            Candidate.organization_id == int(organization_id),
            Candidate.deleted_at.is_(None),
        )),
        CandidateApplication.role.has(and_(
            Role.organization_id == int(organization_id),
            Role.role_kind == ROLE_KIND_STANDARD,
            Role.ats_owner_role_id.is_(None),
            Role.deleted_at.is_(None),
        )),
    )


def order_columns(*, sort_by: str, sort_order: str):
    """Order a related roster by its local score or stage activity."""

    direction = asc if sort_order == "asc" else desc
    primary = (
        SisterRoleEvaluation.pipeline_stage_updated_at
        if sort_by == "pipeline_stage_updated_at"
        else SisterRoleEvaluation.role_fit_score
    )
    return (
        direction(primary).nullslast(),
        direction(CandidateApplication.created_at).nullslast(),
        direction(CandidateApplication.id),
    )


def last_activity_at(
    db: Session,
    *,
    organization_id: int,
    roster_role_id: int,
    view_role_id: int,
    related: bool,
):
    """Return last visible activity using the stage owner for this view."""

    if related:
        activity_at = func.coalesce(
            SisterRoleEvaluation.pipeline_stage_updated_at,
            SisterRoleEvaluation.updated_at,
            SisterRoleEvaluation.created_at,
        )
    else:
        activity_at = func.coalesce(
            CandidateApplication.pipeline_stage_updated_at,
            CandidateApplication.updated_at,
            CandidateApplication.created_at,
        )
    query = db.query(func.max(activity_at)).select_from(CandidateApplication)
    if related:
        query = query.outerjoin(
            SisterRoleEvaluation,
            and_(
                SisterRoleEvaluation.role_id == int(view_role_id),
                SisterRoleEvaluation.source_application_id == CandidateApplication.id,
            ),
        ).filter(valid_source_scope(
            organization_id=organization_id, owner_role_id=roster_role_id,
        ))
    return query.filter(
        CandidateApplication.organization_id == int(organization_id),
        CandidateApplication.role_id == int(roster_role_id),
        CandidateApplication.deleted_at.is_(None),
        CandidateApplication.application_outcome == "open",
    ).scalar()


__all__ = ["last_activity_at", "order_columns", "stage_column", "valid_source_scope"]
