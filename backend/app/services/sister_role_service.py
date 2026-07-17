"""Shared persistence and projection helpers for sister roles."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_DONE,
    SISTER_EVAL_EXCLUDED,
    SISTER_EVAL_PENDING,
    SISTER_EVAL_UNSCORABLE,
    SisterRoleEvaluation,
)
from .sister_role_projection import project_sister_application


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


def source_application_is_globally_advanced(
    application: CandidateApplication | None,
) -> bool:
    """Whether the one canonical application has left every Taali funnel."""

    return bool(
        application is not None
        and str(application.pipeline_stage or "").strip().lower() == "advanced"
    )


def _empty_related_pipeline_counts() -> dict[str, int]:
    return {
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
    db: Session, role_ids: list[int]
) -> dict[int, dict[str, int]]:
    """Load independent related-role funnels without a per-role query."""

    role_ids = [int(role_id) for role_id in role_ids]
    counts_by_role = {
        role_id: _empty_related_pipeline_counts() for role_id in role_ids
    }
    if not role_ids:
        return counts_by_role
    rows = (
        db.query(
            SisterRoleEvaluation.role_id,
            SisterRoleEvaluation.pipeline_stage,
            SisterRoleEvaluation.status,
            CandidateApplication.application_outcome,
            CandidateApplication.workable_disqualified,
            CandidateApplication.pipeline_stage,
            func.count(SisterRoleEvaluation.id),
        )
        .join(
            CandidateApplication,
            CandidateApplication.id == SisterRoleEvaluation.source_application_id,
        )
        .filter(SisterRoleEvaluation.role_id.in_(role_ids))
        .group_by(
            SisterRoleEvaluation.role_id,
            SisterRoleEvaluation.pipeline_stage,
            SisterRoleEvaluation.status,
            CandidateApplication.application_outcome,
            CandidateApplication.workable_disqualified,
            CandidateApplication.pipeline_stage,
        )
        .all()
    )
    for role_id, stage, score_status, outcome, disqualified, source_stage, total in rows:
        counts = counts_by_role[int(role_id)]
        total = int(total or 0)
        if str(outcome or "open") != "open" or bool(disqualified):
            counts["rejected"] += total
            continue
        if str(source_stage or "").strip().lower() == "advanced":
            counts["advanced"] += total
            continue
        local_stage = str(stage or "applied")
        if local_stage == "applied" and score_status == SISTER_EVAL_DONE:
            counts["scored"] += total
        elif local_stage == "review":
            counts["completed"] += total
        elif local_stage in counts:
            counts[local_stage] += total
        else:
            counts["applied"] += total
    return counts_by_role


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
    # Advancing the canonical application hands the candidate out of every
    # linked Taali funnel. A late invite/submission callback must never pull a
    # related projection back to invited/review after that shared hand-off.
    if str(evaluation.pipeline_stage or "").strip().lower() == "advanced" and stage != "advanced":
        return evaluation
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
        evaluation = existing.get(int(application.id))
        if (
            source_application_is_globally_advanced(application)
            and not source_application_is_globally_closed(application)
        ):
            # Advanced is a positive terminal hand-off, not an exclusion. Keep
            # any existing score/audit snapshot intact and only stamp the
            # shared terminal stage. A newly linked related role gets a quiet
            # completed projection and never queues paid scoring for it.
            if evaluation is None:
                evaluation = SisterRoleEvaluation(
                    organization_id=role.organization_id,
                    role_id=role.id,
                    source_application_id=application.id,
                    status=SISTER_EVAL_DONE,
                    spec_fingerprint=spec_hash,
                    cv_fingerprint=text_fingerprint(cv_text) if cv_text else None,
                    queued_at=now,
                    pipeline_stage="advanced",
                )
                db.add(evaluation)
            else:
                transition_related_role_stage(
                    evaluation, to_stage="advanced", source="system"
                )
            counts.setdefault(str(evaluation.status), 0)
            counts[str(evaluation.status)] += 1
            continue
        next_status = (
            SISTER_EVAL_EXCLUDED
            if source_application_is_globally_closed(application)
            else (SISTER_EVAL_PENDING if cv_text else SISTER_EVAL_UNSCORABLE)
        )
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
                pipeline_stage="applied",
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
        evaluation = (
            db.query(SisterRoleEvaluation)
            .filter(
                SisterRoleEvaluation.role_id == sister.id,
                SisterRoleEvaluation.source_application_id == application.id,
            )
            .first()
        )
        if (
            source_application_is_globally_advanced(application)
            and not source_application_is_globally_closed(application)
        ):
            if evaluation is None:
                evaluation = SisterRoleEvaluation(
                    organization_id=application.organization_id,
                    role_id=sister.id,
                    source_application_id=application.id,
                    status=SISTER_EVAL_DONE,
                    spec_fingerprint=spec_hash,
                    cv_fingerprint=cv_hash,
                    queued_at=now,
                    pipeline_stage="advanced",
                )
                db.add(evaluation)
            else:
                transition_related_role_stage(
                    evaluation, to_stage="advanced", source="system"
                )
            continue
        next_status = (
            SISTER_EVAL_EXCLUDED
            if source_application_is_globally_closed(application)
            else (SISTER_EVAL_PENDING if cv_text else SISTER_EVAL_UNSCORABLE)
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
                pipeline_stage="applied",
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
            if cv_text and next_status != SISTER_EVAL_EXCLUDED:
                to_score.append(evaluation)
    db.flush()
    return [int(item.id) for item in to_score]


def reconcile_related_roles_after_outcome(
    db: Session, application: CandidateApplication
) -> None:
    """Best-effort propagation of a canonical close/reopen to related roles."""

    try:
        ensure_application_sister_evaluations(db, application)
    except Exception:  # pragma: no cover - canonical outcome must still win
        import logging

        logging.getLogger("taali.pipeline_service").exception(
            "related-role outcome reconcile failed (application_id=%s)",
            application.id,
        )


__all__ = [
    "ensure_application_sister_evaluations",
    "ensure_sister_evaluations",
    "pipeline_counts_for_role",
    "project_sister_application",
    "reconcile_related_roles_after_outcome",
    "related_role_advance_note",
    "related_role_pipeline_counts",
    "related_role_pipeline_counts_bulk",
    "source_application_is_globally_advanced",
    "source_application_is_globally_closed",
    "text_fingerprint",
    "transition_related_role_stage",
]
