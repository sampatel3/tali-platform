"""Role-local funnel state and ATS restriction helpers for related roles."""

from __future__ import annotations

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..candidate_search.population import apply_searchable_candidate_scope
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_DONE,
    SisterRoleEvaluation,
)
from .sister_role_projection import related_role_ats_state


RELATED_ROLE_PIPELINE_STAGES = {
    "sourced", "applied", "invited", "in_assessment", "review", "advanced"
}
RELATED_ROLE_APPLICATION_OUTCOMES = {"open", "rejected", "withdrawn", "hired"}


def source_application_is_globally_closed(
    application: CandidateApplication | None,
) -> bool:
    """Whether shared ATS state restricts another write through this link."""

    if application is None:
        return True
    return (
        str(application.application_outcome or "open") != "open"
        or bool(application.workable_disqualified)
    )


def source_application_is_globally_advanced(
    application: CandidateApplication | None,
) -> bool:
    """Whether shared ATS state is already beyond Taali-controlled hand-off."""

    return bool(
        application is not None
        and str(application.pipeline_stage or "").strip().lower() == "advanced"
    )


def _empty_related_pipeline_counts() -> dict[str, int]:
    return {
        "sourced": 0,
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
    *,
    organization_id: int | None = None,
) -> dict[int, dict[str, int]]:
    """Load funnels from explicit live related-role memberships only."""

    role_ids = [int(role_id) for role_id in role_ids]
    counts_by_role = {
        role_id: _empty_related_pipeline_counts() for role_id in role_ids
    }
    if not role_ids:
        return counts_by_role
    if organization_id is None:
        organization_ids = [
            int(value)
            for (value,) in (
                db.query(Role.organization_id)
                .filter(
                    Role.id.in_(role_ids),
                    Role.deleted_at.is_(None),
                )
                .distinct()
                .all()
            )
        ]
        if len(organization_ids) != 1:
            return counts_by_role
        organization_id = organization_ids[0]

    query = (
        db.query(
            SisterRoleEvaluation.role_id,
            SisterRoleEvaluation.pipeline_stage,
            SisterRoleEvaluation.status,
            SisterRoleEvaluation.application_outcome,
            func.count(SisterRoleEvaluation.id),
        )
        .select_from(SisterRoleEvaluation)
        .join(
            CandidateApplication,
            CandidateApplication.id
            == SisterRoleEvaluation.source_application_id,
        )
        .join(Role, Role.id == SisterRoleEvaluation.role_id)
    )
    query = apply_searchable_candidate_scope(
        query,
        organization_id=int(organization_id),
    )
    rows = (
        query.filter(
            SisterRoleEvaluation.organization_id == int(organization_id),
            SisterRoleEvaluation.role_id.in_(role_ids),
            SisterRoleEvaluation.deleted_at.is_(None),
            CandidateApplication.organization_id == int(organization_id),
            Role.organization_id == int(organization_id),
            or_(
                Role.role_kind == ROLE_KIND_SISTER,
                Role.ats_owner_role_id.isnot(None),
            ),
            Role.deleted_at.is_(None),
        )
        .group_by(
            SisterRoleEvaluation.role_id,
            SisterRoleEvaluation.pipeline_stage,
            SisterRoleEvaluation.status,
            SisterRoleEvaluation.application_outcome,
        )
        .all()
    )
    for role_id, stage, score_status, outcome, total in rows:
        counts = counts_by_role[int(role_id)]
        total = int(total or 0)
        normalized_outcome = str(outcome or "open")
        if normalized_outcome == "rejected":
            counts["rejected"] += total
            continue
        if normalized_outcome != "open":
            continue
        local_stage = str(stage or "applied")
        if local_stage == "applied" and score_status == SISTER_EVAL_DONE:
            counts["scored"] += total
        elif local_stage == "in_assessment":
            counts["invited"] += total
            counts["invited_delivered"] += total
            counts["invited_opened"] += total
            counts["in_assessment"] += total
        elif local_stage == "review":
            counts["completed"] += total
        elif local_stage in counts:
            counts[local_stage] += total
        else:
            counts["applied"] += total
    return counts_by_role


def related_role_pipeline_counts(db: Session, role: Role) -> dict[str, int]:
    """Return one related role's funnel over its explicit live memberships."""

    return related_role_pipeline_counts_bulk(
        db,
        [int(role.id)],
        organization_id=int(role.organization_id),
    )[int(role.id)]


def pipeline_counts_for_role(
    db: Session,
    role: Role,
    *,
    organization_id: int,
    standard_counts: dict[str, int] | None = None,
) -> dict[str, int]:
    """Choose local related-role counts or the canonical role aggregate."""

    if standard_counts is not None:
        return standard_counts
    if (
        str(role.role_kind or "") == ROLE_KIND_SISTER
        or role.ats_owner_role_id is not None
    ):
        return related_role_pipeline_counts(db, role)
    from ..domains.assessments_runtime.pipeline_service import role_pipeline_counts

    return role_pipeline_counts(
        db, organization_id=organization_id, role_id=int(role.id)
    )


def transition_related_role_stage(
    evaluation: SisterRoleEvaluation,
    *,
    to_stage: str,
    source: str,
) -> SisterRoleEvaluation:
    stage = str(to_stage or "").strip().lower()
    if stage not in RELATED_ROLE_PIPELINE_STAGES:
        raise ValueError(f"Unsupported related-role stage: {to_stage}")
    if (
        str(evaluation.pipeline_stage or "").strip().lower() == "advanced"
        and stage != "advanced"
    ):
        return evaluation
    from datetime import datetime, timezone

    evaluation.pipeline_stage = stage
    evaluation.pipeline_stage_source = str(source or "system")
    evaluation.pipeline_stage_updated_at = datetime.now(timezone.utc)
    return evaluation


def transition_related_role_outcome(
    evaluation: SisterRoleEvaluation,
    *,
    to_outcome: str,
    source: str,
) -> SisterRoleEvaluation:
    """Transition exactly one related-role membership's local outcome."""

    outcome = str(to_outcome or "").strip().lower()
    if outcome not in RELATED_ROLE_APPLICATION_OUTCOMES:
        raise ValueError(f"Unsupported related-role outcome: {to_outcome}")
    from datetime import datetime, timezone

    evaluation.application_outcome = outcome
    evaluation.application_outcome_source = str(source or "system")
    evaluation.application_outcome_updated_at = datetime.now(timezone.utc)
    return evaluation


def related_role_action_restrictions(
    *,
    role: Role,
    evaluation: SisterRoleEvaluation,
    source_application: CandidateApplication | None = None,
) -> dict:
    """Return the central shared-ATS restriction contract for one membership."""

    return related_role_ats_state(
        sister_role=role,
        evaluation=evaluation,
        source_application=(source_application or evaluation.source_application),
    )["action_restrictions"]


def related_role_advance_note(role: Role, owner_role: Role | None) -> str:
    related_label = " ".join(str(role.name or "").split()) or "Related role"
    owner_label = (
        " ".join(str(owner_role.name or "").split()) if owner_role is not None else ""
    ) or "Original linked role"
    return (
        "TAALI · Candidate advanced for a related role\n"
        f"Role: {related_label}\n"
        f"Original ATS role: {owner_label}\n"
        "Reason: The candidate met the advance criteria for the related role."
    )


__all__ = [
    "pipeline_counts_for_role",
    "related_role_action_restrictions",
    "related_role_advance_note",
    "related_role_pipeline_counts",
    "related_role_pipeline_counts_bulk",
    "source_application_is_globally_advanced",
    "source_application_is_globally_closed",
    "transition_related_role_outcome",
    "transition_related_role_stage",
]
