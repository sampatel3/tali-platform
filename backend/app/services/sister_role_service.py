"""Shared persistence and projection helpers for sister roles."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_EXCLUDED,
    SISTER_EVAL_PENDING,
    SISTER_EVAL_UNSCORABLE,
    SisterRoleEvaluation,
)
from .sister_role_evaluation_lifecycle import (
    archive_evaluation_result as _archive_evaluation_result,
)
from .sister_role_projection import project_sister_application
from .related_role_pipeline import (
    pipeline_counts_for_role,
    related_role_action_restrictions,
    related_role_advance_note,
    related_role_pipeline_counts,
    related_role_pipeline_counts_bulk,
    source_application_is_globally_advanced,
    source_application_is_globally_closed,
    transition_related_role_outcome,
    transition_related_role_stage,
)
from .related_role_direct_membership import create_direct_related_membership
from .related_role_source import (
    RelatedRoleSourceMember,
    application_cv_text,
    related_role_ats_owner,
    related_role_source_fingerprint,
    select_related_role_source_members,
    text_fingerprint,
)


def operational_role_id(role: Role) -> int:
    return int(role.ats_owner_role_id or role.id)


def ensure_sister_evaluations(
    db: Session,
    role: Role,
    *,
    reset_existing: bool = False,
    seed_missing: bool = False,
    source_role: Role | None = None,
    source_members: Sequence[RelatedRoleSourceMember] | None = None,
) -> dict[str, int]:
    if str(role.role_kind or "") != ROLE_KIND_SISTER:
        raise ValueError("Role is not a related role")

    memberships = (
        db.query(SisterRoleEvaluation)
        .options(
            joinedload(SisterRoleEvaluation.source_application).joinedload(
                CandidateApplication.candidate
            )
        )
        .filter(
            SisterRoleEvaluation.organization_id == int(role.organization_id),
            SisterRoleEvaluation.role_id == int(role.id),
            SisterRoleEvaluation.deleted_at.is_(None),
        )
        .order_by(SisterRoleEvaluation.id.asc())
        .all()
    )
    spec_hash = text_fingerprint(role.job_spec_text)
    now = datetime.now(timezone.utc)

    existing_candidate_ids = {
        int(
            membership.candidate_id
            or getattr(membership.source_application, "candidate_id", 0)
            or 0
        )
        for membership in memberships
    }
    if seed_missing:
        explicit_source = source_role
        if explicit_source is None:
            source_id = (
                getattr(role, "related_source_role_id", None)
                or role.ats_owner_role_id
            )
            explicit_source = db.get(Role, int(source_id)) if source_id else None
        selected_members = (
            list(source_members)
            if source_members is not None
            else (
                select_related_role_source_members(db, explicit_source)
                if explicit_source is not None
                else []
            )
        )
        for source_member in selected_members:
            candidate_id = int(source_member.candidate_id)
            if candidate_id in existing_candidate_ids:
                continue
            application = source_member.source_application
            cv_text = application_cv_text(application)
            locally_active = (
                str(source_member.application_outcome).strip().lower() == "open"
                and str(source_member.pipeline_stage).strip().lower() != "advanced"
            )
            next_status = (
                SISTER_EVAL_EXCLUDED
                if not locally_active
                else SISTER_EVAL_PENDING
                if cv_text
                else SISTER_EVAL_UNSCORABLE
            )
            membership = SisterRoleEvaluation(
                organization_id=int(role.organization_id),
                role_id=int(role.id),
                candidate_id=candidate_id,
                source_application_id=int(application.id),
                ats_application_id=source_member.ats_application_id,
                status=next_status,
                pipeline_stage=source_member.pipeline_stage,
                pipeline_stage_updated_at=source_member.pipeline_stage_updated_at or now,
                pipeline_stage_source=source_member.pipeline_stage_source,
                application_outcome=source_member.application_outcome,
                application_outcome_updated_at=(
                    source_member.application_outcome_updated_at or now
                ),
                application_outcome_source=source_member.application_outcome_source,
                membership_source="initial_snapshot",
                spec_fingerprint=spec_hash,
                cv_fingerprint=text_fingerprint(cv_text) if cv_text else None,
                queued_at=now,
                error_message=(
                    "Source membership was not active at snapshot"
                    if not locally_active
                    else None
                    if cv_text
                    else "No CV text available"
                ),
            )
            db.add(membership)
            memberships.append(membership)
            existing_candidate_ids.add(candidate_id)

    if reset_existing:
        for evaluation in memberships:
            if (
                str(evaluation.application_outcome or "open").strip().lower()
                != "open"
                or str(evaluation.pipeline_stage or "applied").strip().lower()
                == "advanced"
            ):
                continue
            application = evaluation.source_application
            if application is None:
                application = db.get(
                    CandidateApplication, int(evaluation.source_application_id)
                )
            if application is None:
                continue
            cv_text = application_cv_text(application)
            next_status = SISTER_EVAL_PENDING if cv_text else SISTER_EVAL_UNSCORABLE
            _archive_evaluation_result(evaluation)
            evaluation.status = next_status
            evaluation.spec_fingerprint = spec_hash
            evaluation.cv_fingerprint = text_fingerprint(cv_text) if cv_text else None
            evaluation.role_fit_score = None
            evaluation.summary = None
            evaluation.details = None
            evaluation.error_message = None if cv_text else "No CV text available"
            evaluation.cache_hit = False
            evaluation.attempts = 0
            evaluation.next_attempt_at = None
            evaluation.dispatch_attempted_at = None
            evaluation.last_error_code = None
            evaluation.queued_at = now
            evaluation.started_at = None
            evaluation.scored_at = None

    db.flush()
    counts = {"total": len(memberships), "pending": 0, "unscorable": 0}
    for evaluation in memberships:
        counts.setdefault(str(evaluation.status), 0)
        counts[str(evaluation.status)] += 1
    return counts


def ensure_application_sister_evaluations(
    db: Session,
    application: CandidateApplication,
    *,
    sister_roles: list[Role] | None = None,
    queue_for_rescore: bool = False,
) -> list[int]:
    """Refresh memberships already linked to a changed source application.

    This function never adds a candidate to a related role. Row existence is
    explicit membership; new owner applications therefore cannot fan out into
    every related pool. Passive refreshes hold changed score inputs for an
    explicit recruiter re-evaluation. Only a caller that sets
    ``queue_for_rescore`` may receive work ids to publish after commit.
    """
    evaluation_query = db.query(SisterRoleEvaluation).filter(
        SisterRoleEvaluation.organization_id == int(application.organization_id),
        SisterRoleEvaluation.deleted_at.is_(None),
        SisterRoleEvaluation.application_outcome == "open",
        SisterRoleEvaluation.pipeline_stage != "advanced",
        or_(
            SisterRoleEvaluation.source_application_id == int(application.id),
            SisterRoleEvaluation.ats_application_id == int(application.id),
            SisterRoleEvaluation.candidate_id == int(application.candidate_id),
        ),
    )
    if sister_roles is not None:
        allowed_role_ids = [int(sister.id) for sister in sister_roles]
        if not allowed_role_ids:
            return []
        evaluation_query = evaluation_query.filter(
            SisterRoleEvaluation.role_id.in_(allowed_role_ids)
        )
    evaluations = evaluation_query.order_by(SisterRoleEvaluation.id.asc()).all()
    if not evaluations:
        return []
    evaluation_role_ids = {int(evaluation.role_id) for evaluation in evaluations}
    sisters = (
        list(sister_roles)
        if sister_roles is not None
        else db.query(Role)
        .filter(
            Role.id.in_(evaluation_role_ids),
            Role.organization_id == int(application.organization_id),
            Role.role_kind == ROLE_KIND_SISTER,
            Role.deleted_at.is_(None),
        )
        .all()
    )
    role_by_id = {
        int(sister.id): sister
        for sister in sisters
        if int(sister.id) in evaluation_role_ids
    }
    if not role_by_id:
        return []
    to_score: list[SisterRoleEvaluation] = []
    for evaluation in evaluations:
        sister = role_by_id.get(int(evaluation.role_id))
        if sister is None:
            continue
        evaluation.candidate_id = int(application.candidate_id)
        if (
            evaluation.ats_application_id is None
            and int(sister.ats_owner_role_id or 0) == int(application.role_id)
        ):
            evaluation.ats_application_id = int(application.id)
        # A matching ATS application can establish the restriction link for a
        # direct membership, but only its explicit evidence/source application
        # may invalidate the role-local score.
        if int(evaluation.source_application_id) != int(application.id):
            continue
        cv_text = application_cv_text(application)
        cv_hash = text_fingerprint(cv_text) if cv_text else None
        spec_hash = text_fingerprint(sister.job_spec_text)
        if (
            queue_for_rescore
            or evaluation.cv_fingerprint != cv_hash
            or evaluation.spec_fingerprint != spec_hash
            or evaluation.status == SISTER_EVAL_EXCLUDED
        ):
            from .sister_role_evaluation_lifecycle import (
                reset_evaluation_for_rescore,
            )

            can_dispatch = reset_evaluation_for_rescore(
                evaluation,
                role_id=int(evaluation.role_id),
                application_id=int(evaluation.source_application_id),
                cv_text=cv_text,
                job_spec=str(sister.job_spec_text or ""),
                hold_for_explicit_release=not queue_for_rescore,
            )
            if can_dispatch:
                to_score.append(evaluation)
    db.flush()
    return [int(item.id) for item in to_score]


def reconcile_related_roles_after_outcome(
    db: Session, application: CandidateApplication
) -> None:
    """Refresh evidence and ATS links without changing role-local state."""

    try:
        # Owner pipeline/outcome changes may establish an ATS restriction link,
        # but they are never authority to clear or re-score role-local results.
        evaluations = (
            db.query(SisterRoleEvaluation)
            .join(Role, Role.id == SisterRoleEvaluation.role_id)
            .filter(
                SisterRoleEvaluation.organization_id
                == int(application.organization_id),
                SisterRoleEvaluation.deleted_at.is_(None),
                or_(
                    SisterRoleEvaluation.source_application_id
                    == int(application.id),
                    SisterRoleEvaluation.ats_application_id
                    == int(application.id),
                    SisterRoleEvaluation.candidate_id
                    == int(application.candidate_id),
                ),
                Role.organization_id == int(application.organization_id),
                Role.role_kind == ROLE_KIND_SISTER,
                Role.ats_owner_role_id == int(application.role_id),
                Role.deleted_at.is_(None),
            )
            .all()
        )
        for evaluation in evaluations:
            if evaluation.candidate_id is None:
                evaluation.candidate_id = int(application.candidate_id)
            if evaluation.ats_application_id is None:
                evaluation.ats_application_id = int(application.id)
        db.flush()
    except Exception:  # pragma: no cover - canonical outcome must still win
        import logging

        logging.getLogger("taali.pipeline_service").exception(
            "related-role outcome reconcile failed (application_id=%s)",
            application.id,
        )


__all__ = [
    "create_direct_related_membership",
    "ensure_application_sister_evaluations",
    "ensure_sister_evaluations",
    "pipeline_counts_for_role",
    "project_sister_application",
    "RelatedRoleSourceMember",
    "related_role_ats_owner",
    "related_role_source_fingerprint",
    "select_related_role_source_members",
    "reconcile_related_roles_after_outcome",
    "related_role_action_restrictions",
    "related_role_advance_note",
    "related_role_pipeline_counts",
    "related_role_pipeline_counts_bulk",
    "source_application_is_globally_advanced",
    "source_application_is_globally_closed",
    "text_fingerprint",
    "transition_related_role_outcome",
    "transition_related_role_stage",
]
