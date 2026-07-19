"""Detached snapshots and short persistence transactions for submission."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...domains.assessments_runtime.pipeline_service import (
    ensure_pipeline_fields,
    initialize_pipeline_event_if_missing,
    transition_stage,
)
from ...domains.assessments_runtime.role_support import refresh_application_score_cache
from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...models.task import Task
from ...models.user import User
from ...platform.config import settings


def _deepcopy(value: Any) -> Any:
    return deepcopy(value)


def _column_values(row: Any) -> dict[str, Any]:
    return {
        column.key: _deepcopy(getattr(row, column.key))
        for column in row.__table__.columns
    }


class MutableProviderRecord:
    """Column-only mutable DTO; no SQLAlchemy state or lazy relationships."""

    def __init__(self, values: dict[str, Any]):
        object.__setattr__(self, "_values", _deepcopy(values))
        object.__setattr__(self, "_original", _deepcopy(values))

    def __getattr__(self, name: str) -> Any:
        values = object.__getattribute__(self, "_values")
        if name in values:
            return values[name]
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        values = object.__getattribute__(self, "_values")
        values[name] = _deepcopy(value)

    def changed_columns(self) -> dict[str, Any]:
        original = object.__getattribute__(self, "_original")
        values = object.__getattribute__(self, "_values")
        return {
            key: _deepcopy(value)
            for key, value in values.items()
            if key in original and value != original[key]
        }

    def mark_persisted(self, *names: str) -> None:
        original = object.__getattribute__(self, "_original")
        values = object.__getattribute__(self, "_values")
        for name in names:
            original[name] = _deepcopy(values.get(name))


@dataclass
class SubmissionProviderSnapshot:
    assessment: MutableProviderRecord
    task: MutableProviderRecord
    application: MutableProviderRecord | None
    candidate_present: bool
    cv_text: str | None
    job_spec_text: str | None
    criteria_payload: list[dict[str, Any]]
    additional_requirements: str | None
    role_name: str | None
    authority_fingerprint: str


@dataclass(frozen=True)
class SubmissionSideEffects:
    assessment_id: int
    notify_email: str | None
    candidate_name: str
    workable_payload: dict[str, Any] | None


_IDENTITY_COLUMNS = {
    "id",
    "organization_id",
    "candidate_id",
    "task_id",
    "role_id",
    "application_id",
    "token",
    "created_at",
    "updated_at",
}


def _stable_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _criteria_payload(role: Role | None) -> list[dict[str, Any]]:
    if role is None:
        return []
    return [
        {
            "id": int(criterion.id),
            "text": str(criterion.text or "").strip(),
            "must_have": bool(criterion.must_have),
            "source": str(criterion.source or "recruiter"),
            "ordering": int(getattr(criterion, "ordering", 0) or 0),
            "deleted_at": getattr(criterion, "deleted_at", None),
        }
        for criterion in sorted(
            role.criteria or [], key=lambda item: getattr(item, "ordering", 0)
        )
    ]


def _load_rows(
    db: Session,
    *,
    assessment_id: int,
    terminal_statuses: Iterable[AssessmentStatus],
) -> tuple[Assessment, Task, CandidateApplication | None, Candidate | None, Role | None]:
    assessment = (
        db.query(Assessment)
        .filter(
            Assessment.id == int(assessment_id),
            Assessment.status.in_(set(terminal_statuses)),
        )
        .populate_existing()
        .with_for_update(of=Assessment)
        .one_or_none()
    )
    if assessment is None:
        raise HTTPException(
            status_code=409,
            detail="Assessment submission authority changed",
        )
    task = db.query(Task).filter(Task.id == assessment.task_id).one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    application = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.id == assessment.application_id)
        .one_or_none()
        if assessment.application_id
        else None
    )
    candidate = (
        db.query(Candidate).filter(Candidate.id == assessment.candidate_id).one_or_none()
        if assessment.candidate_id
        else None
    )
    role = (
        db.query(Role).filter(Role.id == assessment.role_id).one_or_none()
        if assessment.role_id
        else None
    )
    return assessment, task, application, candidate, role


def _authority_payload(
    assessment: Assessment,
    task: Task,
    application: CandidateApplication | None,
    candidate: Candidate | None,
    role: Role | None,
) -> dict[str, Any]:
    return {
        "assessment": _column_values(assessment),
        "task": _column_values(task),
        "application": _column_values(application) if application is not None else None,
        "candidate": _column_values(candidate) if candidate is not None else None,
        "role": _column_values(role) if role is not None else None,
        "criteria": _criteria_payload(role),
    }


def _assert_authority(
    snapshot: SubmissionProviderSnapshot,
    rows: tuple[Assessment, Task, CandidateApplication | None, Candidate | None, Role | None],
) -> None:
    if _stable_fingerprint(_authority_payload(*rows)) != snapshot.authority_fingerprint:
        raise HTTPException(
            status_code=409,
            detail="Assessment inputs changed while submission providers were running",
        )


def snapshot_terminal_submission(
    db: Session,
    *,
    assessment_id: int,
    terminal_statuses: Iterable[AssessmentStatus],
) -> SubmissionProviderSnapshot:
    """Capture every provider input, then release the request transaction."""

    rows = _load_rows(
        db,
        assessment_id=assessment_id,
        terminal_statuses=terminal_statuses,
    )
    assessment, task, application, candidate, role = rows
    criteria = _criteria_payload(role)
    active_criteria = [item for item in criteria if item.get("deleted_at") is None]
    try:
        from ...services.role_criteria_service import render_role_intent_lines

        intent_lines = render_role_intent_lines(role) if role is not None else []
    except Exception:
        intent_lines = []
    cv_text = (
        getattr(application, "cv_text", None)
        or getattr(candidate, "cv_text", None)
    )
    job_spec_text = (
        getattr(role, "job_spec_text", None)
        or getattr(candidate, "job_spec_text", None)
    )
    snapshot = SubmissionProviderSnapshot(
        assessment=MutableProviderRecord(_column_values(assessment)),
        task=MutableProviderRecord(_column_values(task)),
        application=(
            MutableProviderRecord(_column_values(application))
            if application is not None
            else None
        ),
        candidate_present=candidate is not None,
        cv_text=cv_text,
        job_spec_text=job_spec_text,
        criteria_payload=[
            {
                "id": item["id"],
                "text": item["text"],
                "must_have": item["must_have"],
                "source": item["source"],
            }
            for item in active_criteria
        ],
        additional_requirements="\n".join(intent_lines) or None,
        role_name=getattr(role, "name", None),
        authority_fingerprint=_stable_fingerprint(_authority_payload(*rows)),
    )
    db.rollback()
    return snapshot


def persist_submission_git_checkpoint(
    db: Session,
    snapshot: SubmissionProviderSnapshot,
    *,
    terminal_statuses: Iterable[AssessmentStatus],
    git_evidence: dict[str, Any],
    final_repo_state: str | None,
) -> None:
    """Persist the recovery head in one fresh exact-authority transaction."""

    rows = _load_rows(
        db,
        assessment_id=int(snapshot.assessment.id),
        terminal_statuses=terminal_statuses,
    )
    _assert_authority(snapshot, rows)
    assessment = rows[0]
    assessment.git_evidence = _deepcopy(git_evidence)
    assessment.final_repo_state = final_repo_state
    db.flush()
    db.refresh(assessment)
    snapshot.assessment.git_evidence = git_evidence
    snapshot.assessment.final_repo_state = final_repo_state
    snapshot.assessment.mark_persisted("git_evidence", "final_repo_state")
    snapshot.authority_fingerprint = _stable_fingerprint(_authority_payload(*rows))
    db.commit()


def finalize_submission_snapshot(
    db: Session,
    snapshot: SubmissionProviderSnapshot,
    *,
    terminal_statuses: Iterable[AssessmentStatus],
    retry_scoring: bool,
    grading_incomplete: bool,
    suppress_completion_side_effects: bool,
    request_id: str | None = None,
    settings_obj: Any = settings,
) -> SubmissionSideEffects:
    """Apply DTO changes and pipeline state in one fresh final transaction."""

    rows = _load_rows(
        db,
        assessment_id=int(snapshot.assessment.id),
        terminal_statuses=terminal_statuses,
    )
    _assert_authority(snapshot, rows)
    assessment, _task, application, candidate, _role = rows
    for key, value in snapshot.assessment.changed_columns().items():
        if key not in _IDENTITY_COLUMNS:
            setattr(assessment, key, _deepcopy(value))

    if application is not None:
        from ...services.related_role_application_runtime import (
            assessment_uses_related_role_pipeline,
            transition_related_role_assessment_stage,
        )

        if assessment_uses_related_role_pipeline(db, assessment):
            if not grading_incomplete:
                transition_related_role_assessment_stage(
                    db,
                    assessment=assessment,
                    to_stage="review",
                    source="system",
                )
        else:
            ensure_pipeline_fields(application)
            initialize_pipeline_event_if_missing(
                db,
                app=application,
                actor_type="system",
                reason="Pipeline initialized at assessment submit",
            )
            if not grading_incomplete:
                transition_stage(
                    db,
                    app=application,
                    to_stage="review",
                    source="system",
                    actor_type="system",
                    reason=(
                        "Assessment grading completed"
                        if retry_scoring
                        else "Assessment completed"
                    ),
                    metadata={
                        "assessment_id": int(assessment.id),
                        "completed_due_to_timeout": bool(
                            assessment.completed_due_to_timeout
                        ),
                    },
                )
            refresh_application_score_cache(application, db=db)

    notify_email = None
    if not grading_incomplete and not suppress_completion_side_effects:
        notify_user = (
            db.query(User)
            .filter(
                User.organization_id == assessment.organization_id,
                User.is_active.is_(True),
            )
            .order_by(User.is_superuser.desc(), User.created_at.asc())
            .first()
        )
        notify_email = str(notify_user.email) if notify_user is not None else None
    candidate_name = (
        str(candidate.full_name or candidate.email)
        if candidate is not None
        else "Candidate"
    )

    workable_payload = None
    if not grading_incomplete and not suppress_completion_side_effects:
        from .result_delivery_outbox import (
            attach_assessment_result_delivery_receipt,
        )

        dispatch = attach_assessment_result_delivery_receipt(
            db,
            assessment,
            request_id=request_id,
            settings_obj=settings_obj,
        )
        if dispatch is not None:
            workable_payload = {
                "assessment_id": dispatch.assessment_id,
                "organization_id": dispatch.organization_id,
                "operation_id": dispatch.operation_id,
            }
    result = SubmissionSideEffects(
        assessment_id=int(assessment.id),
        notify_email=notify_email,
        candidate_name=candidate_name,
        workable_payload=workable_payload,
    )
    db.commit()
    return result


__all__ = [
    "MutableProviderRecord",
    "SubmissionProviderSnapshot",
    "SubmissionSideEffects",
    "finalize_submission_snapshot",
    "persist_submission_git_checkpoint",
    "snapshot_terminal_submission",
]
