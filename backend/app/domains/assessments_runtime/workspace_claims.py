"""Immutable DB claims for detached candidate-workspace provider calls."""

from __future__ import annotations

import hashlib
import json
import secrets
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...models.assessment import Assessment, AssessmentStatus
from ...models.task import Task


class WorkspaceClaimDriftError(RuntimeError):
    """Assessment or task authority changed while provider work was running."""


@dataclass(frozen=True, slots=True)
class TaskWorkspaceSnapshot:
    id: int
    organization_id: int | None
    name: str
    description: str | None
    duration_minutes: int | None
    starter_code: str | None
    task_key: str | None
    role: str | None
    scenario: str | None
    repo_structure: dict[str, Any] | None
    evaluation_rubric: dict[str, Any] | None
    extra_data: dict[str, Any] | None
    proctoring_enabled: bool
    claude_budget_limit_usd: float | None
    is_active: bool
    is_template: bool
    fingerprint: str


@dataclass(frozen=True, slots=True)
class AssessmentWorkspaceClaim:
    id: int
    organization_id: int | None
    candidate_id: int | None
    task_id: int
    role_id: int | None
    application_id: int | None
    token: str
    status: AssessmentStatus
    started_at: datetime | None
    expires_at: datetime | None
    duration_minutes: int | None
    is_timer_paused: bool
    paused_at: datetime | None
    pause_reason: str | None
    total_paused_seconds: int
    e2b_session_id: str | None
    assessment_repo_url: str | None
    assessment_branch: str | None
    clone_command: str | None
    ai_mode: str | None
    is_demo: bool
    is_voided: bool
    task: TaskWorkspaceSnapshot


def _task_payload(task: Task) -> dict[str, Any]:
    return {
        "id": int(task.id),
        "organization_id": (
            int(task.organization_id) if task.organization_id is not None else None
        ),
        "name": str(task.name),
        "description": task.description,
        "duration_minutes": task.duration_minutes,
        "starter_code": task.starter_code,
        "task_key": task.task_key,
        "role": task.role,
        "scenario": task.scenario,
        "repo_structure": deepcopy(task.repo_structure),
        "evaluation_rubric": deepcopy(task.evaluation_rubric),
        "extra_data": deepcopy(task.extra_data),
        "proctoring_enabled": bool(task.proctoring_enabled),
        "claude_budget_limit_usd": task.claude_budget_limit_usd,
        "is_active": bool(task.is_active),
        "is_template": bool(task.is_template),
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


def task_workspace_snapshot(task: Task) -> TaskWorkspaceSnapshot:
    payload = _task_payload(task)
    fingerprint = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    payload.pop("updated_at", None)
    return TaskWorkspaceSnapshot(**payload, fingerprint=fingerprint)


def assessment_workspace_claim(
    assessment: Assessment,
    task: Task,
) -> AssessmentWorkspaceClaim:
    return AssessmentWorkspaceClaim(
        id=int(assessment.id),
        organization_id=(
            int(assessment.organization_id)
            if assessment.organization_id is not None
            else None
        ),
        candidate_id=(
            int(assessment.candidate_id)
            if assessment.candidate_id is not None
            else None
        ),
        task_id=int(assessment.task_id),
        role_id=int(assessment.role_id) if assessment.role_id is not None else None,
        application_id=(
            int(assessment.application_id)
            if assessment.application_id is not None
            else None
        ),
        token=str(assessment.token or ""),
        status=assessment.status,
        started_at=assessment.started_at,
        expires_at=assessment.expires_at,
        duration_minutes=assessment.duration_minutes,
        is_timer_paused=bool(assessment.is_timer_paused),
        paused_at=assessment.paused_at,
        pause_reason=assessment.pause_reason,
        total_paused_seconds=int(assessment.total_paused_seconds or 0),
        e2b_session_id=assessment.e2b_session_id,
        assessment_repo_url=assessment.assessment_repo_url,
        assessment_branch=assessment.assessment_branch,
        clone_command=assessment.clone_command,
        ai_mode=assessment.ai_mode,
        is_demo=bool(assessment.is_demo),
        is_voided=bool(assessment.is_voided),
        task=task_workspace_snapshot(task),
    )


def claim_matches_assessment(
    assessment: Assessment,
    claim: AssessmentWorkspaceClaim,
) -> bool:
    return bool(
        int(assessment.id) == claim.id
        and secrets.compare_digest(str(assessment.token or ""), claim.token)
        and assessment.status == claim.status
        and not bool(assessment.is_voided)
        and assessment.organization_id == claim.organization_id
        and assessment.candidate_id == claim.candidate_id
        and int(assessment.task_id) == claim.task_id
        and assessment.role_id == claim.role_id
        and assessment.application_id == claim.application_id
        and assessment.started_at == claim.started_at
        and assessment.expires_at == claim.expires_at
        and assessment.duration_minutes == claim.duration_minutes
        and bool(assessment.is_timer_paused) == claim.is_timer_paused
        and assessment.paused_at == claim.paused_at
        and assessment.pause_reason == claim.pause_reason
        and int(assessment.total_paused_seconds or 0) == claim.total_paused_seconds
        and assessment.e2b_session_id == claim.e2b_session_id
        and assessment.assessment_repo_url == claim.assessment_repo_url
        and assessment.assessment_branch == claim.assessment_branch
        and assessment.clone_command == claim.clone_command
        and assessment.ai_mode == claim.ai_mode
        and bool(assessment.is_demo) == claim.is_demo
    )


def lock_and_revalidate_workspace_claim(
    db: Session,
    claim: AssessmentWorkspaceClaim,
) -> tuple[Assessment, Task]:
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == claim.id)
        .populate_existing()
        .with_for_update(of=Assessment)
        .one_or_none()
    )
    if assessment is None or not claim_matches_assessment(assessment, claim):
        raise WorkspaceClaimDriftError("assessment authority changed")
    task = (
        db.query(Task)
        .filter(Task.id == claim.task_id)
        .populate_existing()
        .with_for_update(of=Task)
        .one_or_none()
    )
    if (
        task is None
        or task_workspace_snapshot(task).fingerprint != claim.task.fingerprint
    ):
        raise WorkspaceClaimDriftError("assessment task changed")
    return assessment, task


__all__ = [
    "AssessmentWorkspaceClaim",
    "TaskWorkspaceSnapshot",
    "WorkspaceClaimDriftError",
    "assessment_workspace_claim",
    "claim_matches_assessment",
    "lock_and_revalidate_workspace_claim",
    "task_workspace_snapshot",
]
