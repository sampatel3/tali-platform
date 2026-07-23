"""Candidate-row projection for full related roles."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_PENDING,
    SisterRoleEvaluation,
)


def _resolve_related_role_ats_application(
    *,
    sister_role: Role,
    evaluation: SisterRoleEvaluation | None,
    source_application: CandidateApplication | None,
) -> tuple[CandidateApplication | None, str | None]:
    """Resolve one transport and a fail-closed reason when unavailable.

    ``ats_application_id`` is valid only for the membership's tenant and
    candidate and for the logical role's currently declared ATS owner. The
    source fallback is limited to rolling compatibility rows that have no
    explicit transport id; it is subject to the exact same identity checks.
    """

    if evaluation is None:
        return None, "ats_application_unlinked"
    if int(evaluation.role_id) != int(sister_role.id):
        return None, "ats_application_invalid"
    if int(evaluation.organization_id) != int(sister_role.organization_id):
        return None, "ats_application_invalid"
    owner_role_id = sister_role.ats_owner_role_id
    if owner_role_id is None:
        return None, "ats_application_unlinked"
    application = (
        evaluation._ats_application_record
        if evaluation.ats_application_id is not None
        else source_application
    )
    if application is None:
        return None, "ats_application_unlinked"
    if int(application.organization_id) != int(evaluation.organization_id):
        return None, "ats_application_invalid"
    if int(application.candidate_id) != int(evaluation.candidate_id):
        return None, "ats_application_invalid"
    if int(application.role_id) != int(owner_role_id):
        return None, "ats_application_wrong_owner"
    if getattr(application, "deleted_at", None) is not None:
        return None, "ats_application_deleted"
    owner_role = sister_role.ats_owner_role
    if (
        owner_role is None
        or int(owner_role.organization_id) != int(evaluation.organization_id)
        or getattr(owner_role, "deleted_at", None) is not None
    ):
        return None, "ats_owner_role_unavailable"
    return application, None


def validated_related_role_ats_application(
    *,
    sister_role: Role,
    evaluation: SisterRoleEvaluation | None,
    source_application: CandidateApplication | None,
) -> CandidateApplication | None:
    """Return only a live, fully identity-validated ATS transport."""

    application, _reason = _resolve_related_role_ats_application(
        sister_role=sister_role,
        evaluation=evaluation,
        source_application=source_application,
    )
    return application


def related_role_ats_state(
    *,
    sister_role: Role,
    evaluation: SisterRoleEvaluation | None,
    source_application: CandidateApplication | None,
) -> dict:
    """Return shared ATS state as restrictions, never as local state.

    Migration 185 gives every persisted membership an explicit
    ``ats_application_id``. The source-application fallback keeps mixed-version
    rolling deploys and old test fixtures readable without restoring the old
    owner-stage/outcome projection.
    """

    ats_application, unavailable_code = _resolve_related_role_ats_application(
        sister_role=sister_role,
        evaluation=evaluation,
        source_application=source_application,
    )

    if ats_application is None:
        ats_context = {
            "provider": "native",
            "raw_stage": None,
            "normalized_stage": None,
            "needs_mapping": False,
            "post_handover": False,
            "writeback_linked": False,
            "application_outcome": None,
            "workable_disqualified": False,
        }
        restriction_codes = [unavailable_code or "ats_application_unlinked"]
    else:
        # Lazy import avoids the pipeline_service -> sister_role_service ->
        # projection -> ats_context_service -> pipeline_service import cycle.
        from .ats_context_service import application_ats_context

        ats_context = application_ats_context(ats_application)
        ats_outcome = str(
            ats_application.application_outcome or "open"
        ).strip().lower()
        ats_context.update(
            {
                "application_outcome": ats_outcome,
                "workable_disqualified": bool(
                    ats_application.workable_disqualified
                ),
            }
        )
        restriction_codes: list[str] = []
        if ats_outcome != "open":
            restriction_codes.append(f"shared_ats_outcome_{ats_outcome}")
        if bool(ats_application.workable_disqualified):
            restriction_codes.append("shared_ats_disqualified")
        if bool(ats_context.get("post_handover")):
            restriction_codes.append("shared_ats_post_handover")
        if not bool(ats_context.get("writeback_linked")):
            restriction_codes.append("ats_writeback_unlinked")

    can_advance_in_ats = not restriction_codes
    restrictions = {
        "restricted": bool(restriction_codes),
        "codes": restriction_codes,
        # Local decisions belong to this logical role and remain available
        # regardless of shared ATS state. Reject is deliberately never written
        # to the shared ATS application because that would alter another role.
        "can_advance_locally": True,
        "can_reject_locally": True,
        "can_write_to_ats": can_advance_in_ats,
        "can_advance_in_ats": can_advance_in_ats,
        "can_reject_in_ats": False,
    }
    return {
        "ats_context": ats_context,
        "action_restrictions": restrictions,
    }


def project_sister_application(
    payload: dict,
    *,
    sister_role: Role,
    owner_role: Role | None,
    evaluation: SisterRoleEvaluation | None,
    db: Session | None = None,
    application: CandidateApplication | None = None,
    assessments: list | None = None,
    pending_decision: object | None = None,
    runtime_preloaded: bool = False,
) -> dict:
    """Overlay role-local score/funnel state on the shared ATS application."""

    projected = dict(payload)
    score = evaluation.role_fit_score if evaluation is not None else None
    status = evaluation.status if evaluation is not None else SISTER_EVAL_PENDING
    details = evaluation.details if evaluation is not None else None
    requirements_score = (
        details.get("requirements_match_score_100")
        if isinstance(details, dict)
        else None
    )
    if not isinstance(requirements_score, (int, float)):
        requirements_score = score
    # Build this from the role-owned evaluation, never by extending the source
    # application's score summary. Extending it retained owner integrity,
    # component, provenance and assessment judgments under otherwise-correct
    # related-role headline scores.
    summary = {
        "taali_score": score,
        "role_fit_score": score,
        "cv_fit_score": score,
        "requirements_fit_score": requirements_score,
        "assessment_score": None,
        "assessment_id": None,
        "assessment_status": None,
        "mode": "sister_role",
        "score_mode": "sister_role",
        "score_provenance": {
            "source": "sister_role_evaluation",
            "label": "Related role fit",
            "scored_at": (
                evaluation.scored_at.isoformat()
                if evaluation is not None and evaluation.scored_at is not None
                else None
            ),
            "model": evaluation.model_version if evaluation is not None else None,
        },
        "role_fit_components": {
            "cv_fit_score": score,
            "requirements_fit_score": requirements_score,
        },
    }
    local_stage = (
        str(evaluation.pipeline_stage or "applied")
        if evaluation is not None
        else "applied"
    )
    local_outcome = (
        str(evaluation.application_outcome or "open").strip().lower()
        if evaluation is not None
        else "open"
    )
    source_application = application
    if source_application is None and evaluation is not None:
        source_application = evaluation.source_application
    ats_state = related_role_ats_state(
        sister_role=sister_role,
        evaluation=evaluation,
        source_application=source_application,
    )
    ats_context = ats_state["ats_context"]
    ats_application = validated_related_role_ats_application(
        sister_role=sister_role,
        evaluation=evaluation,
        source_application=source_application,
    )
    if (
        str(ats_context.get("application_outcome") or "open") != "open"
        or bool(ats_context.get("workable_disqualified"))
    ):
        legacy_availability = "disqualified"
    elif bool(ats_context.get("post_handover")):
        legacy_availability = "external_advanced"
    else:
        legacy_availability = "active"
    projected.update(
        {
            "role_id": sister_role.id,
            "role_name": sister_role.name,
            # The optional owner is an ATS transport boundary, not the
            # candidate's operational role. Transport restrictions are exposed
            # only through ``ats_context`` / ``action_restrictions`` below.
            "operational_role_id": None,
            "operational_role_name": None,
            "sister_role_id": sister_role.id,
            # The source application's score is a judgment from another role.
            # Keep ATS linkage in ``ats_context`` but never expose that verdict
            # through the related-role candidate payload.
            "source_role_score": None,
            "status": local_stage,
            "pipeline_stage": local_stage,
            "pipeline_stage_updated_at": (
                evaluation.pipeline_stage_updated_at
                if evaluation is not None
                else None
            ),
            "pipeline_stage_source": (
                evaluation.pipeline_stage_source
                if evaluation is not None
                else "system"
            ),
            "application_outcome": local_outcome,
            "application_outcome_updated_at": (
                evaluation.application_outcome_updated_at
                if evaluation is not None
                else None
            ),
            "application_outcome_source": (
                evaluation.application_outcome_source
                if evaluation is not None
                else "system"
            ),
            "version": int(evaluation.version or 1) if evaluation is not None else 1,
            # Shared external state is a restriction boundary only. It never
            # becomes this role's membership, pipeline stage, or outcome.
            "ats_context": ats_context,
            "action_restrictions": ats_state["action_restrictions"],
            # Provider fields belong to the validated ATS transport. A direct
            # role-local source application may contain similarly named fields,
            # but they are not this role's external state.
            "workable_stage": (
                ats_application.workable_stage if ats_application is not None else None
            ),
            "bullhorn_status": (
                ats_application.bullhorn_status if ats_application is not None else None
            ),
            "external_stage_raw": (
                ats_application.external_stage_raw
                if ats_application is not None
                else None
            ),
            "external_stage_normalized": (
                ats_application.external_stage_normalized
                if ats_application is not None
                else None
            ),
            "workable_profile_url": (
                ats_application.workable_profile_url
                if ats_application is not None
                else None
            ),
            # Backward-compatible summary enum. The detailed restriction map
            # above is authoritative and local actions remain available.
            "related_role_availability": legacy_availability,
            "taali_score": score,
            "rank_score": score,
            "pre_screen_score": score,
            "requirements_fit_score": requirements_score,
            "cv_match_score": score,
            "cv_match_details": details,
            "cv_match_scored_at": (
                evaluation.scored_at if evaluation is not None else None
            ),
            "score_status": status,
            "score_mode": "sister_role",
            "score_summary": summary,
            "valid_assessment_id": None,
            "valid_assessment_status": None,
            "assessment_preview": None,
            "assessment_history": [],
            "pending_decision": None,
            # Application-column judgments and history belong to the physical
            # source role. Related notes/actions live in role-attributed events;
            # related assessments and pending decisions are overlaid below.
            "manual_decision": (
                evaluation.manual_decision if evaluation is not None else None
            ),
            "notes": None,
            "pre_screen_recommendation": None,
            "pre_screen_evidence": None,
            "auto_reject_state": None,
            "auto_reject_reason": None,
            "auto_reject_triggered_at": None,
            "workable_score": None,
            "workable_score_raw": None,
            "workable_score_source": None,
            "workable_disqualified": False,
            "workable_disqualified_at": None,
            "candidate_interview_kit": None,
            "screening_pack": None,
            "tech_interview_pack": None,
            "screening_interview_summary": None,
            "tech_interview_summary": None,
            "interview_evidence_summary": None,
            "interviews": [],
            "interview_feedback": [],
            "workable_comments": [],
            "workable_questionnaire_answers": [],
            "workable_activity_log": [],
            "pipeline_external_drift": False,
            # Membership time, not the source application's age, is this role's
            # application history boundary.
            "created_at": evaluation.created_at if evaluation is not None else None,
            "applied_at": evaluation.created_at if evaluation is not None else None,
            "updated_at": evaluation.updated_at if evaluation is not None else None,
            "last_activity_at": (
                evaluation.updated_at
                or evaluation.application_outcome_updated_at
                or evaluation.pipeline_stage_updated_at
                or evaluation.scored_at
                or evaluation.created_at
                if evaluation is not None
                else None
            ),
        }
    )
    if db is not None:
        from .related_role_application_runtime import (
            apply_related_role_runtime_projection,
        )

        apply_related_role_runtime_projection(
            db,
            projected=projected,
            payload=payload,
            sister_role=sister_role,
            evaluation=evaluation,
            role_fit_score=score,
            application=application,
            assessments=assessments,
            pending_decision=pending_decision,
            runtime_preloaded=runtime_preloaded,
        )
    return projected


__all__ = [
    "project_sister_application",
    "related_role_ats_state",
    "validated_related_role_ats_application",
]
