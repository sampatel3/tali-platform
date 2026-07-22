"""Creation and restoration of direct related-role memberships."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models.assessment import Assessment
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_EXCLUDED,
    SISTER_EVAL_PENDING,
    SISTER_EVAL_UNSCORABLE,
    SisterRoleEvaluation,
)
from .related_role_source import application_cv_text, text_fingerprint
from .sister_role_evaluation_lifecycle import archive_evaluation_result


_SOURCED_ERROR_CODE = "sourced_prospect"
_SOURCED_ERROR_MESSAGE = "Sourced prospects are not scored until they apply"


def _is_sourced(application: CandidateApplication) -> bool:
    return str(application.pipeline_stage or "").strip().lower() == "sourced"


def _reset_sourced_evaluation(
    evaluation: SisterRoleEvaluation,
    *,
    spec_fingerprint: str,
    now: datetime,
    archive: bool,
) -> None:
    """Keep a pre-application membership visible without authorising scoring."""

    if archive:
        archive_evaluation_result(evaluation)
    evaluation.status = SISTER_EVAL_UNSCORABLE
    evaluation.spec_fingerprint = spec_fingerprint
    # Deliberately do not fingerprint a candidate-level CV while this role
    # membership is only sourced. Moving to applied must invalidate this hold.
    evaluation.cv_fingerprint = None
    evaluation.role_fit_score = None
    evaluation.summary = None
    evaluation.details = None
    evaluation.model_version = None
    evaluation.prompt_version = None
    evaluation.trace_id = None
    evaluation.cache_hit = False
    evaluation.attempts = 0
    evaluation.next_attempt_at = None
    evaluation.dispatch_attempted_at = None
    evaluation.queued_at = now
    evaluation.started_at = None
    evaluation.scored_at = None
    evaluation.last_error_code = _SOURCED_ERROR_CODE
    evaluation.error_message = _SOURCED_ERROR_MESSAGE


def create_direct_related_membership(
    db: Session,
    *,
    role: Role,
    application: CandidateApplication,
) -> SisterRoleEvaluation:
    """Create or restore one direct related-role membership lifecycle."""

    if (
        str(role.role_kind or "") != ROLE_KIND_SISTER
        or role.deleted_at is not None
        or int(application.organization_id) != int(role.organization_id)
        or int(application.role_id) != int(role.id)
        or application.deleted_at is not None
    ):
        raise ValueError("Application is not a direct related-role application")

    application = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application.id),
            CandidateApplication.organization_id == int(role.organization_id),
            CandidateApplication.role_id == int(role.id),
            CandidateApplication.candidate_id == int(application.candidate_id),
            CandidateApplication.deleted_at.is_(None),
        )
        .with_for_update(of=CandidateApplication)
        .one()
    )
    ats_application = None
    if role.ats_owner_role_id is not None:
        ats_application = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.organization_id == int(role.organization_id),
                CandidateApplication.role_id == int(role.ats_owner_role_id),
                CandidateApplication.candidate_id == int(application.candidate_id),
                CandidateApplication.deleted_at.is_(None),
            )
            .order_by(CandidateApplication.id.desc())
            .with_for_update(of=CandidateApplication)
            .first()
        )
    existing = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.organization_id == int(role.organization_id),
            SisterRoleEvaluation.role_id == int(role.id),
            or_(
                SisterRoleEvaluation.candidate_id == int(application.candidate_id),
                SisterRoleEvaluation.source_application_id == int(application.id),
            ),
        )
        .with_for_update(of=SisterRoleEvaluation)
        .one_or_none()
    )
    if existing is not None:
        if existing.deleted_at is None:
            if (
                int(existing.candidate_id or 0) != int(application.candidate_id)
                or int(existing.source_application_id) != int(application.id)
            ):
                raise ValueError(
                    "A live related-role membership cannot be rebound to another application"
                )
            if _is_sourced(application):
                _reset_sourced_evaluation(
                    existing,
                    spec_fingerprint=text_fingerprint(role.job_spec_text),
                    now=datetime.now(timezone.utc),
                    archive=True,
                )
                db.flush()
            return existing

        now = datetime.now(timezone.utc)
        prior_application_id = int(existing.source_application_id)
        prior_stage = str(existing.pipeline_stage or "applied")
        prior_outcome = str(existing.application_outcome or "open")
        prior_version = int(existing.version or 0)
        cv_text = application_cv_text(application)
        job_spec = str(role.job_spec_text or "").strip()

        archive_evaluation_result(existing)
        existing.deleted_at = None
        existing.candidate_id = int(application.candidate_id)
        existing.source_application_id = int(application.id)
        existing.ats_application_id = (
            int(ats_application.id) if ats_application is not None else None
        )
        existing.membership_source = "direct"
        existing.pipeline_stage = str(application.pipeline_stage or "applied")
        existing.pipeline_stage_updated_at = (
            application.pipeline_stage_updated_at or now
        )
        existing.pipeline_stage_source = str(
            application.pipeline_stage_source or "system"
        )
        existing.application_outcome = str(
            application.application_outcome or "open"
        )
        existing.application_outcome_updated_at = (
            application.application_outcome_updated_at or now
        )
        existing.application_outcome_source = "system"
        existing.version = max(prior_version, 1) + 1

        existing.spec_fingerprint = text_fingerprint(job_spec)
        existing.cv_fingerprint = text_fingerprint(cv_text) if cv_text else None
        existing.role_fit_score = None
        existing.summary = None
        existing.details = None
        existing.model_version = None
        existing.prompt_version = None
        existing.trace_id = None
        existing.cache_hit = False
        existing.attempts = 0
        existing.next_attempt_at = None
        existing.dispatch_attempted_at = None
        existing.queued_at = now
        existing.started_at = None
        existing.scored_at = None
        locally_active = (
            str(existing.application_outcome).strip().lower() == "open"
            and str(existing.pipeline_stage).strip().lower() != "advanced"
        )
        if not locally_active:
            existing.status = SISTER_EVAL_EXCLUDED
            existing.last_error_code = "direct_application_not_active"
            existing.error_message = (
                "Direct application was restored outside the active candidate funnel"
            )
        elif _is_sourced(application):
            _reset_sourced_evaluation(
                existing,
                spec_fingerprint=text_fingerprint(job_spec),
                now=now,
                archive=False,
            )
        elif not cv_text or not job_spec:
            existing.status = SISTER_EVAL_UNSCORABLE
            existing.last_error_code = (
                "missing_cv_text" if not cv_text else "missing_job_specification"
            )
            existing.error_message = (
                "No CV text available"
                if not cv_text
                else "No job specification available"
            )
        else:
            existing.status = SISTER_EVAL_PENDING
            existing.last_error_code = None
            existing.error_message = None

        prior_assessments = (
            db.query(Assessment)
            .filter(
                Assessment.organization_id == int(role.organization_id),
                Assessment.role_id == int(role.id),
                Assessment.candidate_id == int(application.candidate_id),
                Assessment.is_voided.is_(False),
            )
            .with_for_update(of=Assessment)
            .all()
        )
        for assessment in prior_assessments:
            assessment.is_voided = True
            assessment.voided_at = now
            assessment.void_reason = (
                "Superseded when the candidate re-applied to this role"
            )

        from ..domains.assessments_runtime.pipeline_service import (
            append_application_event,
        )
        from .pre_screen_decision_emitter import discard_pending_decisions_for_app

        for application_id in {prior_application_id, int(application.id)}:
            discard_pending_decisions_for_app(
                db,
                application_id=application_id,
                role_id=int(role.id),
                reason="superseded: candidate started a new role membership lifecycle",
                include_processing=True,
            )
        append_application_event(
            db,
            app=application,
            role_id=int(role.id),
            event_type="related_role_membership_restored",
            actor_type="system",
            from_stage=prior_stage,
            to_stage=existing.pipeline_stage,
            from_outcome=prior_outcome,
            to_outcome=existing.application_outcome,
            reason="Candidate re-applied to this related role",
            metadata={
                "acting_role_id": int(role.id),
                "membership_id": int(existing.id),
                "previous_source_application_id": prior_application_id,
                "source_application_id": int(application.id),
                "membership_version": int(existing.version),
            },
            effect_status="succeeded",
            idempotency_key=(
                f"related-membership-restored:{int(existing.id)}:"
                f"v{int(existing.version)}"
            ),
        )
        db.flush()
        return existing

    cv_text = application_cv_text(application)
    job_spec = str(role.job_spec_text or "").strip()
    now = datetime.now(timezone.utc)
    pipeline_stage = str(application.pipeline_stage or "applied")
    application_outcome = str(application.application_outcome or "open")
    sourced = _is_sourced(application)
    locally_active = (
        application_outcome.strip().lower() == "open"
        and pipeline_stage.strip().lower() != "advanced"
    )
    if not locally_active:
        membership_status = SISTER_EVAL_EXCLUDED
        last_error_code = "direct_application_not_active"
        error_message = (
            "Direct application was restored outside the active candidate funnel"
        )
    elif sourced:
        membership_status = SISTER_EVAL_UNSCORABLE
        last_error_code = _SOURCED_ERROR_CODE
        error_message = _SOURCED_ERROR_MESSAGE
    elif not cv_text or not job_spec:
        membership_status = SISTER_EVAL_UNSCORABLE
        last_error_code = (
            "missing_cv_text" if not cv_text else "missing_job_specification"
        )
        error_message = (
            "No CV text available"
            if not cv_text
            else "No job specification available"
        )
    else:
        membership_status = SISTER_EVAL_PENDING
        last_error_code = None
        error_message = None
    membership = SisterRoleEvaluation(
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        candidate_id=int(application.candidate_id),
        source_application_id=int(application.id),
        ats_application_id=(
            int(ats_application.id) if ats_application is not None else None
        ),
        status=membership_status,
        pipeline_stage=pipeline_stage,
        pipeline_stage_updated_at=application.pipeline_stage_updated_at or now,
        pipeline_stage_source=str(application.pipeline_stage_source or "system"),
        application_outcome=application_outcome,
        application_outcome_updated_at=(
            application.application_outcome_updated_at or now
        ),
        application_outcome_source=str(application.pipeline_stage_source or "system"),
        membership_source="direct",
        spec_fingerprint=text_fingerprint(job_spec),
        cv_fingerprint=(
            None if sourced else text_fingerprint(cv_text) if cv_text else None
        ),
        queued_at=now,
        last_error_code=last_error_code,
        error_message=error_message,
    )
    db.add(membership)
    db.flush()
    return membership


__all__ = ["create_direct_related_membership"]
