"""Candidate-row projection for full related roles."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_PENDING,
    SisterRoleEvaluation,
)


_POST_HANDOVER_STAGES = {
    "phone_screen",
    "phone_interview",
    "first_stage",
    "interview",
    "technical",
    "technical_interview",
    "final_interview",
    "onsite",
    "presentation",
    "assessment",
    "offer",
    "offer_extended",
    "offer_accepted",
    "hired",
}


def _stage(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def project_sister_application(
    payload: dict,
    *,
    sister_role: Role,
    owner_role: Role,
    evaluation: SisterRoleEvaluation | None,
    db: Session | None = None,
    application: CandidateApplication | None = None,
    assessments: list | None = None,
    pending_decision: object | None = None,
    runtime_preloaded: bool = False,
) -> dict:
    """Overlay role-local score/funnel state on the shared ATS application."""

    projected = dict(payload)
    original_score = payload.get("taali_score")
    score = evaluation.role_fit_score if evaluation is not None else None
    status = evaluation.status if evaluation is not None else SISTER_EVAL_PENDING
    details = evaluation.details if evaluation is not None else None
    summary = dict(payload.get("score_summary") or {})
    summary.update(
        {
            "taali_score": score,
            "role_fit_score": score,
            "cv_fit_score": score,
            "assessment_score": None,
            "assessment_id": None,
            "assessment_status": None,
            "score_mode": "sister_role",
            "score_provenance": {
                "source": "sister_role_evaluation",
                "label": "Related role fit",
            },
        }
    )
    canonical_outcome = str(payload.get("application_outcome") or "open").strip().lower()
    disqualified = canonical_outcome == "rejected" or (
        canonical_outcome == "open" and bool(payload.get("workable_disqualified"))
    )
    globally_closed = canonical_outcome != "open"
    globally_advanced = _stage(payload.get("pipeline_stage")) == "advanced"
    local_stage = (
        "advanced"
        if globally_advanced
        else (
            str(evaluation.pipeline_stage or "applied")
            if evaluation is not None
            else "applied"
        )
    )
    external_advanced = globally_advanced or _stage(
        payload.get("workable_stage")
    ) in _POST_HANDOVER_STAGES
    projected.update(
        {
            "role_id": sister_role.id,
            "role_name": sister_role.name,
            "operational_role_id": owner_role.id,
            "operational_role_name": owner_role.name,
            "sister_role_id": sister_role.id,
            "source_role_score": original_score,
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
            # Outcome is canonical shared ATS state. Availability carries the
            # broader action lock, so hired/withdrawn/disqualified candidates
            # are never relabeled as rejections in only the related projection.
            "application_outcome": canonical_outcome,
            "related_role_availability": (
                "disqualified"
                if disqualified
                else (
                    "closed"
                    if globally_closed
                    else ("external_advanced" if external_advanced else "active")
                )
            ),
            "taali_score": score,
            "pre_screen_score": score,
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


__all__ = ["project_sister_application"]
