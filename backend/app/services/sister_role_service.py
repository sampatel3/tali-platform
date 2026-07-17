"""Shared persistence and projection helpers for sister roles."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_EXCLUDED,
    SISTER_EVAL_PENDING,
    SISTER_EVAL_UNSCORABLE,
    SisterRoleEvaluation,
)
from .related_role_roster import (
    active_source_applications_for_related_role,
    related_role_pipeline_counts_bulk,
)


RELATED_ROLE_PIPELINE_STAGES = {
    "applied", "invited", "in_assessment", "review", "advanced"
}


def source_application_is_globally_closed(
    application: CandidateApplication | None,
) -> bool:
    """Whether the shared ATS application is unavailable in every role."""

    if application is None:
        return True
    return (
        str(application.application_outcome or "open") != "open"
        or bool(application.workable_disqualified)
    )


def related_role_pipeline_counts(db: Session, role: Role) -> dict[str, int]:
    """Return one related role's local funnel; canonical rejection wins."""

    return related_role_pipeline_counts_bulk(db, [int(role.id)])[int(role.id)]


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
    if str(role.role_kind or "") == ROLE_KIND_SISTER:
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
    evaluation.pipeline_stage = stage
    evaluation.pipeline_stage_source = str(source or "system")
    evaluation.pipeline_stage_updated_at = datetime.now(timezone.utc)
    return evaluation


def related_role_advance_note(role: Role, owner_role: Role | None) -> str:
    owner_label = (
        f"{owner_role.name} #{owner_role.id}"
        if owner_role is not None
        else "the original linked role"
    )
    related_label = f"{role.name} #{role.id}"
    return (
        f"Advanced for related role: {related_label}. Taali assessed this candidate "
        f"in the independent {related_label} funnel. The ATS application is shared "
        f"with {owner_label}."
    )


def text_fingerprint(value: str | None) -> str:
    return hashlib.sha256((value or "").strip().encode("utf-8")).hexdigest()


def application_cv_text(application: CandidateApplication) -> str:
    return (
        (application.cv_text or "").strip()
        or (
            (application.candidate.cv_text or "").strip()
            if application.candidate is not None
            else ""
        )
    )


def operational_role_id(role: Role) -> int:
    return int(role.ats_owner_role_id or role.id)


def archive_sister_evaluation_result(evaluation: SisterRoleEvaluation) -> None:
    if evaluation.scored_at is None and evaluation.role_fit_score is None and not evaluation.details:
        return
    history = list(evaluation.history or [])
    snapshot = {
        "status": evaluation.status,
        "role_fit_score": evaluation.role_fit_score,
        "summary": evaluation.summary,
        "spec_fingerprint": evaluation.spec_fingerprint,
        "cv_fingerprint": evaluation.cv_fingerprint,
        "model_version": evaluation.model_version,
        "prompt_version": evaluation.prompt_version,
        "trace_id": evaluation.trace_id,
        "cache_hit": bool(evaluation.cache_hit),
        "scored_at": evaluation.scored_at.isoformat() if evaluation.scored_at else None,
    }
    # A stale result may be archived when the spec changes and encountered
    # again by a later full-roster manual re-score. Keep one provenance record
    # for the same scored artifact instead of inflating history with copies.
    if history and all(
        history[-1].get(key) == snapshot.get(key)
        for key in (
            "role_fit_score",
            "summary",
            "spec_fingerprint",
            "cv_fingerprint",
            "model_version",
            "prompt_version",
            "trace_id",
            "scored_at",
        )
    ):
        return
    history.append(snapshot)
    evaluation.history = history[-20:]


def ensure_sister_evaluations(
    db: Session,
    role: Role,
    *,
    reset_existing: bool = False,
) -> dict[str, int]:
    if str(role.role_kind or "") != ROLE_KIND_SISTER or not role.ats_owner_role_id:
        raise ValueError("Role is not a coupled related role")

    applications = active_source_applications_for_related_role(db, role)
    existing = {
        int(item.source_application_id): item
        for item in db.query(SisterRoleEvaluation).filter(
            SisterRoleEvaluation.role_id == role.id
        ).all()
    }
    current_application_ids = {int(application.id) for application in applications}
    for source_application_id, evaluation in existing.items():
        if source_application_id not in current_application_ids:
            evaluation.status = SISTER_EVAL_EXCLUDED
            evaluation.error_message = "Source application left the owner roster"
            evaluation.last_error_code = "source_application_outside_owner_roster"
    spec_hash = text_fingerprint(role.job_spec_text)
    now = datetime.now(timezone.utc)
    counts = {"total": len(applications), "pending": 0, "unscorable": 0}
    for application in applications:
        cv_text = application_cv_text(application)
        next_status = (
            SISTER_EVAL_EXCLUDED
            if source_application_is_globally_closed(application)
            else (SISTER_EVAL_PENDING if cv_text else SISTER_EVAL_UNSCORABLE)
        )
        evaluation = existing.get(int(application.id))
        if evaluation is None:
            evaluation = SisterRoleEvaluation(
                organization_id=role.organization_id,
                role_id=role.id,
                source_application_id=application.id,
                status=next_status,
                spec_fingerprint=spec_hash,
                cv_fingerprint=text_fingerprint(cv_text) if cv_text else None,
                queued_at=now,
                error_message=None if cv_text else "No CV text available",
            )
            db.add(evaluation)
        elif reset_existing or (
            evaluation.status == SISTER_EVAL_EXCLUDED
            and next_status != SISTER_EVAL_EXCLUDED
        ):
            archive_sister_evaluation_result(evaluation)
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
        counts.setdefault(next_status, 0)
        counts[next_status] += 1
    db.flush()
    return counts


def ensure_application_sister_evaluations(
    db: Session,
    application: CandidateApplication,
    *,
    sister_roles: list[Role] | None = None,
) -> list[int]:
    """Queue a new/changed source application for every coupled sister view.

    Returns evaluation ids that need worker dispatch. The caller owns commit
    timing and must commit before publishing those ids to a worker.
    """
    sisters = sister_roles
    if sisters is None:
        sisters = (
            db.query(Role)
            .filter(
                Role.organization_id == application.organization_id,
                Role.role_kind == ROLE_KIND_SISTER,
                Role.ats_owner_role_id == application.role_id,
                Role.deleted_at.is_(None),
            )
            .all()
        )
    if not sisters:
        return []
    cv_text = application_cv_text(application)
    cv_hash = text_fingerprint(cv_text) if cv_text else None
    now = datetime.now(timezone.utc)
    to_score: list[SisterRoleEvaluation] = []
    for sister in sisters:
        spec_hash = text_fingerprint(sister.job_spec_text)
        next_status = (
            SISTER_EVAL_EXCLUDED
            if source_application_is_globally_closed(application)
            else (SISTER_EVAL_PENDING if cv_text else SISTER_EVAL_UNSCORABLE)
        )
        evaluation = (
            db.query(SisterRoleEvaluation)
            .filter(
                SisterRoleEvaluation.role_id == sister.id,
                SisterRoleEvaluation.source_application_id == application.id,
            )
            .first()
        )
        if evaluation is None:
            evaluation = SisterRoleEvaluation(
                organization_id=application.organization_id,
                role_id=sister.id,
                source_application_id=application.id,
                status=next_status,
                spec_fingerprint=spec_hash,
                cv_fingerprint=cv_hash,
                queued_at=now,
                error_message=None if cv_text else "No CV text available",
            )
            db.add(evaluation)
            if cv_text and next_status != SISTER_EVAL_EXCLUDED:
                to_score.append(evaluation)
        elif (
            evaluation.cv_fingerprint != cv_hash
            or evaluation.spec_fingerprint != spec_hash
            or (
                evaluation.status == SISTER_EVAL_EXCLUDED
                and next_status != SISTER_EVAL_EXCLUDED
            )
            or (
                evaluation.status != SISTER_EVAL_EXCLUDED
                and next_status == SISTER_EVAL_EXCLUDED
            )
        ):
            if next_status == SISTER_EVAL_EXCLUDED:
                evaluation.status = SISTER_EVAL_EXCLUDED
                evaluation.error_message = "Shared ATS application is disqualified or closed"
                evaluation.last_error_code = "shared_application_closed"
                evaluation.next_attempt_at = None
                evaluation.dispatch_attempted_at = None
                evaluation.started_at = None
                continue
            archive_sister_evaluation_result(evaluation)
            evaluation.status = next_status
            evaluation.spec_fingerprint = spec_hash
            evaluation.cv_fingerprint = cv_hash
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
            if cv_text and next_status != SISTER_EVAL_EXCLUDED:
                to_score.append(evaluation)
    db.flush()
    return [int(item.id) for item in to_score]


def reconcile_related_roles_after_outcome(
    db: Session, application: CandidateApplication
) -> None:
    """Best-effort propagation of a canonical close/reopen to related roles."""

    try:
        # A reconciliation flush can fail independently of the canonical
        # outcome. Isolate it so rolling back this savepoint leaves the outer
        # outcome transaction usable and authoritative.
        with db.begin_nested():
            ensure_application_sister_evaluations(db, application)
    except Exception:  # pragma: no cover - canonical outcome must still win
        import logging

        logging.getLogger("taali.pipeline_service").exception(
            "related-role outcome reconcile failed (application_id=%s)",
            application.id,
        )


def project_sister_application(
    payload: dict,
    *,
    sister_role: Role,
    owner_role: Role,
    evaluation: SisterRoleEvaluation | None,
) -> dict:
    """Overlay the sister score while preserving the source application id."""
    projected = dict(payload)
    original_score = payload.get("taali_score")
    score = evaluation.role_fit_score if evaluation is not None else None
    status = evaluation.status if evaluation is not None else SISTER_EVAL_PENDING
    details = evaluation.details if evaluation is not None else None
    summary = dict(payload.get("score_summary") or {})
    summary.update({
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
    })
    # The canonical application state is already present in the serialized
    # payload. Reading it here avoids lazy-loading the source application once
    # per row when projecting a large related-role roster.
    canonical_outcome = str(
        payload.get("application_outcome") or "open"
    ).strip().lower()
    workable_disqualified = bool(payload.get("workable_disqualified"))
    projected_outcome = (
        canonical_outcome
        if canonical_outcome != "open"
        else ("rejected" if workable_disqualified else "open")
    )
    local_stage = (
        str(evaluation.pipeline_stage or "applied")
        if evaluation is not None
        else "applied"
    )
    projected.update({
        "role_id": sister_role.id,
        "role_name": sister_role.name,
        "operational_role_id": owner_role.id,
        "operational_role_name": owner_role.name,
        "sister_role_id": sister_role.id,
        "source_role_score": original_score,
        "pipeline_stage": local_stage,
        "pipeline_stage_updated_at": (
            evaluation.pipeline_stage_updated_at if evaluation is not None else None
        ),
        "pipeline_stage_source": (
            evaluation.pipeline_stage_source if evaluation is not None else "system"
        ),
        "application_outcome": projected_outcome,
        "related_role_availability": (
            "disqualified"
            if projected_outcome == "rejected"
            else (
                "closed"
                if projected_outcome != "open"
                else (
                    "external_advanced"
                    if str(payload.get("workable_stage") or "").strip().lower().replace("-", "_").replace(" ", "_")
                    in {
                        "phone_screen", "phone_interview", "first_stage", "interview",
                        "technical", "technical_interview", "final_interview", "onsite",
                        "presentation", "assessment", "offer", "offer_extended",
                        "offer_accepted", "hired",
                    }
                    else "active"
                )
            )
        ),
        "taali_score": score,
        "pre_screen_score": score,
        "cv_match_score": score,
        "cv_match_details": details,
        "cv_match_scored_at": evaluation.scored_at if evaluation is not None else None,
        "score_status": status,
        "score_mode": "sister_role",
        "score_summary": summary,
        "valid_assessment_id": None,
        "valid_assessment_status": None,
        "assessment_preview": None,
        "assessment_history": [],
        "pending_decision": None,
    })
    return projected
