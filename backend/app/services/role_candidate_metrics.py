"""Batched logical candidate metrics for the Jobs role catalogue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import SisterRoleEvaluation
from .related_role_pipeline import related_role_pipeline_counts_bulk


@dataclass(frozen=True, slots=True)
class RoleCandidateMetrics:
    application_counts: dict[int, int]
    active_counts: dict[int, int]
    last_activity: dict[int, datetime | None]
    stage_counts: dict[int, dict[str, int]]


def load_role_candidate_metrics(
    db: Session,
    *,
    roles: list[Role],
    organization_id: int,
    include_pipeline_stats: bool,
) -> RoleCandidateMetrics:
    """Load standard applications and related memberships without pool leakage."""

    related_role_ids = [
        int(role.id)
        for role in roles
        if str(role.role_kind or "") == ROLE_KIND_SISTER
    ]
    related_ids = set(related_role_ids)
    standard_role_ids = [
        int(role.id) for role in roles if int(role.id) not in related_ids
    ]
    app_counts = {
        int(role_id): int(total)
        for role_id, total in (
            db.query(
                CandidateApplication.role_id,
                func.count(CandidateApplication.id),
            )
            .filter(
                CandidateApplication.organization_id == int(organization_id),
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.role_id.in_(standard_role_ids or [-1]),
            )
            .group_by(CandidateApplication.role_id)
            .all()
        )
    }
    if related_role_ids:
        app_counts.update(
            {
                int(role_id): int(total)
                for role_id, total in (
                    db.query(
                        SisterRoleEvaluation.role_id,
                        func.count(SisterRoleEvaluation.id),
                    )
                    .filter(
                        SisterRoleEvaluation.organization_id == int(organization_id),
                        SisterRoleEvaluation.role_id.in_(related_role_ids),
                        SisterRoleEvaluation.deleted_at.is_(None),
                    )
                    .group_by(SisterRoleEvaluation.role_id)
                    .all()
                )
            }
        )
    if not include_pipeline_stats:
        return RoleCandidateMetrics(app_counts, {}, {}, {})

    active_counts = {
        int(role_id): int(total)
        for role_id, total in (
            db.query(
                CandidateApplication.role_id,
                func.count(CandidateApplication.id),
            )
            .filter(
                CandidateApplication.organization_id == int(organization_id),
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.application_outcome == "open",
                CandidateApplication.role_id.in_(standard_role_ids or [-1]),
            )
            .group_by(CandidateApplication.role_id)
            .all()
        )
    }
    if related_role_ids:
        active_counts.update(
            {
                int(role_id): int(total)
                for role_id, total in (
                    db.query(
                        SisterRoleEvaluation.role_id,
                        func.count(SisterRoleEvaluation.id),
                    )
                    .filter(
                        SisterRoleEvaluation.organization_id == int(organization_id),
                        SisterRoleEvaluation.role_id.in_(related_role_ids),
                        SisterRoleEvaluation.deleted_at.is_(None),
                        SisterRoleEvaluation.application_outcome == "open",
                    )
                    .group_by(SisterRoleEvaluation.role_id)
                    .all()
                )
            }
        )

    last_activity = {
        int(role_id): timestamp
        for role_id, timestamp in (
            db.query(
                CandidateApplication.role_id,
                func.max(
                    func.coalesce(
                        CandidateApplication.pipeline_stage_updated_at,
                        CandidateApplication.updated_at,
                        CandidateApplication.created_at,
                    )
                ),
            )
            .filter(
                CandidateApplication.organization_id == int(organization_id),
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.role_id.in_(standard_role_ids or [-1]),
            )
            .group_by(CandidateApplication.role_id)
            .all()
        )
    }
    if related_role_ids:
        last_activity.update(
            {
                int(role_id): timestamp
                for role_id, timestamp in (
                    db.query(
                        SisterRoleEvaluation.role_id,
                        func.max(
                            func.coalesce(
                                SisterRoleEvaluation.pipeline_stage_updated_at,
                                SisterRoleEvaluation.application_outcome_updated_at,
                                SisterRoleEvaluation.updated_at,
                                SisterRoleEvaluation.created_at,
                            )
                        ),
                    )
                    .filter(
                        SisterRoleEvaluation.organization_id == int(organization_id),
                        SisterRoleEvaluation.role_id.in_(related_role_ids),
                        SisterRoleEvaluation.deleted_at.is_(None),
                    )
                    .group_by(SisterRoleEvaluation.role_id)
                    .all()
                )
            }
        )

    from ..domains.assessments_runtime.pipeline_service import (
        role_pipeline_counts_bulk,
    )

    stage_counts = role_pipeline_counts_bulk(
        db,
        organization_id=int(organization_id),
        role_ids=standard_role_ids,
    )
    stage_counts.update(
        related_role_pipeline_counts_bulk(db, related_role_ids)
    )
    return RoleCandidateMetrics(
        app_counts,
        active_counts,
        last_activity,
        stage_counts,
    )


__all__ = ["RoleCandidateMetrics", "load_role_candidate_metrics"]
