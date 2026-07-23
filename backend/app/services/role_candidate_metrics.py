"""Batched logical candidate metrics for the Jobs role catalogue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..candidate_search.population import apply_searchable_candidate_scope
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
        if (
            str(role.role_kind or "") == ROLE_KIND_SISTER
            or role.ats_owner_role_id is not None
        )
    ]
    related_ids = set(related_role_ids)
    standard_role_ids = [
        int(role.id) for role in roles if int(role.id) not in related_ids
    ]
    standard_count_query = db.query(
        CandidateApplication.role_id,
        func.count(CandidateApplication.id),
    )
    standard_count_query = apply_searchable_candidate_scope(
        standard_count_query,
        organization_id=int(organization_id),
    )
    app_counts = {
        int(role_id): int(total)
        for role_id, total in (
            standard_count_query.filter(
                CandidateApplication.organization_id == int(organization_id),
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.role_id.in_(standard_role_ids or [-1]),
            )
            .group_by(CandidateApplication.role_id)
            .all()
        )
    }
    if related_role_ids:
        related_count_query = (
            db.query(
                SisterRoleEvaluation.role_id,
                func.count(SisterRoleEvaluation.id),
            )
            .join(
                CandidateApplication,
                CandidateApplication.id
                == SisterRoleEvaluation.source_application_id,
            )
        )
        related_count_query = apply_searchable_candidate_scope(
            related_count_query,
            organization_id=int(organization_id),
        )
        app_counts.update(
            {
                int(role_id): int(total)
                for role_id, total in (
                    related_count_query.filter(
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

    standard_active_query = db.query(
        CandidateApplication.role_id,
        func.count(CandidateApplication.id),
    )
    standard_active_query = apply_searchable_candidate_scope(
        standard_active_query,
        organization_id=int(organization_id),
    )
    active_counts = {
        int(role_id): int(total)
        for role_id, total in (
            standard_active_query.filter(
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
        related_active_query = (
            db.query(
                SisterRoleEvaluation.role_id,
                func.count(SisterRoleEvaluation.id),
            )
            .join(
                CandidateApplication,
                CandidateApplication.id
                == SisterRoleEvaluation.source_application_id,
            )
        )
        related_active_query = apply_searchable_candidate_scope(
            related_active_query,
            organization_id=int(organization_id),
        )
        active_counts.update(
            {
                int(role_id): int(total)
                for role_id, total in (
                    related_active_query.filter(
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

    standard_activity_query = db.query(
        CandidateApplication.role_id,
        func.max(
            func.coalesce(
                CandidateApplication.pipeline_stage_updated_at,
                CandidateApplication.updated_at,
                CandidateApplication.created_at,
            )
        ),
    )
    standard_activity_query = apply_searchable_candidate_scope(
        standard_activity_query,
        organization_id=int(organization_id),
    )
    last_activity = {
        int(role_id): timestamp
        for role_id, timestamp in (
            standard_activity_query.filter(
                CandidateApplication.organization_id == int(organization_id),
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.role_id.in_(standard_role_ids or [-1]),
            )
            .group_by(CandidateApplication.role_id)
            .all()
        )
    }
    if related_role_ids:
        related_activity_query = (
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
            .join(
                CandidateApplication,
                CandidateApplication.id
                == SisterRoleEvaluation.source_application_id,
            )
        )
        related_activity_query = apply_searchable_candidate_scope(
            related_activity_query,
            organization_id=int(organization_id),
        )
        last_activity.update(
            {
                int(role_id): timestamp
                for role_id, timestamp in (
                    related_activity_query.filter(
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
        related_role_pipeline_counts_bulk(
            db,
            related_role_ids,
            organization_id=int(organization_id),
        )
    )
    return RoleCandidateMetrics(
        app_counts,
        active_counts,
        last_activity,
        stage_counts,
    )


__all__ = ["RoleCandidateMetrics", "load_role_candidate_metrics"]
