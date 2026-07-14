"""Shared persistence and projection helpers for sister roles."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_PENDING,
    SISTER_EVAL_UNSCORABLE,
    SisterRoleEvaluation,
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


def _archive_evaluation_result(evaluation: SisterRoleEvaluation) -> None:
    if evaluation.scored_at is None and evaluation.role_fit_score is None and not evaluation.details:
        return
    history = list(evaluation.history or [])
    history.append({
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
    })
    evaluation.history = history[-20:]


def ensure_sister_evaluations(
    db: Session,
    role: Role,
    *,
    reset_existing: bool = False,
) -> dict[str, int]:
    if str(role.role_kind or "") != ROLE_KIND_SISTER or not role.ats_owner_role_id:
        raise ValueError("Role is not a coupled related role")

    applications = (
        db.query(CandidateApplication)
        .options(joinedload(CandidateApplication.candidate))
        .filter(
            CandidateApplication.organization_id == role.organization_id,
            CandidateApplication.role_id == role.ats_owner_role_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .all()
    )
    existing = {
        int(item.source_application_id): item
        for item in db.query(SisterRoleEvaluation).filter(
            SisterRoleEvaluation.role_id == role.id
        ).all()
    }
    current_application_ids = {int(application.id) for application in applications}
    for source_application_id, evaluation in existing.items():
        if source_application_id not in current_application_ids:
            db.delete(evaluation)
    spec_hash = text_fingerprint(role.job_spec_text)
    now = datetime.now(timezone.utc)
    counts = {"total": len(applications), "pending": 0, "unscorable": 0}
    for application in applications:
        cv_text = application_cv_text(application)
        next_status = SISTER_EVAL_PENDING if cv_text else SISTER_EVAL_UNSCORABLE
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
        elif reset_existing:
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
        next_status = SISTER_EVAL_PENDING if cv_text else SISTER_EVAL_UNSCORABLE
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
            if cv_text:
                to_score.append(evaluation)
        elif evaluation.cv_fingerprint != cv_hash or evaluation.spec_fingerprint != spec_hash:
            _archive_evaluation_result(evaluation)
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
            if cv_text:
                to_score.append(evaluation)
    db.flush()
    return [int(item.id) for item in to_score]


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
    projected.update({
        "role_id": sister_role.id,
        "role_name": sister_role.name,
        "operational_role_id": owner_role.id,
        "operational_role_name": owner_role.name,
        "sister_role_id": sister_role.id,
        "source_role_score": original_score,
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
