"""Detached provider boundary for candidate assessment start and resume."""

from __future__ import annotations

import logging
import secrets
from dataclasses import replace
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...components.assessments.repository import (
    append_assessment_timeline_event,
    ensure_utc,
    utcnow,
)
from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate_application import CandidateApplication
from ...models.role import Role, role_tasks
from ...models.task import Task
from ...services.assessment_repository_service import AssessmentRepositoryError
from ...services.task_repository_serialization import task_repository_write_mutex
from .assessment_start_response import (
    build_assessment_start_response,
    seed_assessment_opener,
)
from .pipeline_service import (
    ensure_pipeline_fields,
    initialize_pipeline_event_if_missing,
    transition_stage,
)
from .workspace_claims import (
    AssessmentWorkspaceClaim,
    WorkspaceClaimDriftError,
    assessment_workspace_claim,
    claim_matches_assessment,
    task_workspace_snapshot,
)
from .workspace_serialization import (
    assessment_workspace_mutex,
    prepare_assessment_workspace_mutex,
)

logger = logging.getLogger(__name__)


def _rollback(db: Session) -> None:
    if db.in_transaction():
        db.rollback()


def _close_new_sandbox(db: Session, e2b: Any, sandbox: Any, assessment_id: int) -> None:
    _rollback(db)
    try:
        e2b.close_sandbox(sandbox)
    except Exception:
        logger.exception(
            "Failed to close provisional E2B sandbox assessment_id=%s",
            assessment_id,
        )


def _validate_startable(assessment: Assessment) -> None:
    if bool(assessment.is_voided):
        raise HTTPException(status_code=400, detail="assessment_voided")
    if assessment.status in {
        AssessmentStatus.COMPLETED,
        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
    }:
        raise HTTPException(
            status_code=400,
            detail="Assessment has already been submitted",
        )
    if assessment.status == AssessmentStatus.EXPIRED:
        raise HTTPException(status_code=400, detail="Assessment link has expired")
    if assessment.status not in {
        AssessmentStatus.PENDING,
        AssessmentStatus.IN_PROGRESS,
    }:
        raise HTTPException(status_code=409, detail="Assessment is not startable")
    if assessment.expires_at and ensure_utc(assessment.expires_at) < utcnow():
        raise HTTPException(status_code=400, detail="Assessment link has expired")


def _capture_start_claim(
    db: Session,
    *,
    assessment_id: int,
    token: str,
) -> AssessmentWorkspaceClaim:
    from ...components.assessments import service as assessment_service

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .populate_existing()
        .with_for_update(of=Assessment)
        .one_or_none()
    )
    if assessment is None or not secrets.compare_digest(
        str(assessment.token or ""),
        token,
    ):
        raise HTTPException(status_code=404, detail="Invalid assessment token")
    _validate_startable(assessment)
    gate = assessment_service.get_assessment_start_gate(
        assessment,
        db,
        lock_organization=True,
    )
    if not gate.get("can_start"):
        raise HTTPException(
            status_code=402,
            detail=assessment_service.INSUFFICIENT_CREDITS_DETAIL,
        )
    task = db.query(Task).filter(Task.id == assessment.task_id).one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    claim = assessment_workspace_claim(assessment, task)
    _rollback(db)
    return claim


def _revalidate_start_claim(
    db: Session,
    claim: AssessmentWorkspaceClaim,
    *,
    require_branch_authority: bool = False,
) -> tuple[Assessment, Task]:
    from ...components.assessments import service as assessment_service

    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == claim.id)
        .populate_existing()
        .with_for_update(of=Assessment)
        .one_or_none()
    )
    if assessment is None or not claim_matches_assessment(assessment, claim):
        raise WorkspaceClaimDriftError("assessment authority changed")
    _validate_startable(assessment)
    gate = assessment_service.get_assessment_start_gate(
        assessment,
        db,
        lock_organization=True,
    )
    if not gate.get("can_start"):
        raise HTTPException(
            status_code=402,
            detail=assessment_service.INSUFFICIENT_CREDITS_DETAIL,
        )
    if require_branch_authority and assessment.role_id is not None:
        role = (
            db.query(Role)
            .filter(
                Role.id == assessment.role_id,
                Role.organization_id == assessment.organization_id,
                Role.deleted_at.is_(None),
            )
            .with_for_update(of=Role)
            .one_or_none()
        )
        link = db.execute(
            role_tasks.select()
            .where(
                role_tasks.c.role_id == assessment.role_id,
                role_tasks.c.task_id == claim.task_id,
            )
            .with_for_update()
        ).first()
        if role is None or link is None:
            raise AssessmentRepositoryError(
                f"Task {claim.task_id} is no longer active and assignable to "
                f"assessment {assessment.id}"
            )
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
    if require_branch_authority and (
        not bool(task.is_active)
        or not (
            task.organization_id == assessment.organization_id
            or (task.organization_id is None and bool(task.is_template))
        )
    ):
        raise AssessmentRepositoryError(
            f"Task {task.id} is no longer active and assignable to "
            f"assessment {assessment.id}"
        )
    return assessment, task


def _ensure_branch(
    db: Session,
    claim: AssessmentWorkspaceClaim,
) -> AssessmentWorkspaceClaim:
    if claim.assessment_branch:
        return claim

    from ...components.assessments import service as assessment_service

    with task_repository_write_mutex(
        db,
        task_id=claim.task_id,
        wait=True,
    ):
        assessment, task = _revalidate_start_claim(
            db,
            claim,
            require_branch_authority=True,
        )
        provider_task = assessment_workspace_claim(assessment, task).task
        _rollback(db)

        repository = assessment_service.AssessmentRepositoryService(
            assessment_service.settings.GITHUB_ORG,
            assessment_service.settings.GITHUB_TOKEN,
        )
        branch = repository.create_assessment_branch(provider_task, claim.id)

        assessment, _task = _revalidate_start_claim(
            db,
            claim,
            require_branch_authority=True,
        )
        assessment.assessment_repo_url = str(branch.repo_url)
        assessment.assessment_branch = str(branch.branch_name)
        assessment.clone_command = str(branch.clone_command)
        db.commit()
        return replace(
            claim,
            assessment_repo_url=str(branch.repo_url),
            assessment_branch=str(branch.branch_name),
            clone_command=str(branch.clone_command),
        )


def _initialize_workspace(
    db: Session,
    claim: AssessmentWorkspaceClaim,
    e2b: Any,
    sandbox: Any,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    from ...components.assessments import service as assessment_service

    try:
        cloned = assessment_service._clone_assessment_branch_into_workspace(
            sandbox,
            claim,
            claim.task,
        )
    except Exception:
        logger.exception(
            "Failed to clone assessment repository assessment_id=%s",
            claim.id,
        )
        if not assessment_service._is_demo_workspace_fallback_enabled(claim):
            raise HTTPException(
                status_code=500,
                detail="Failed to initialize assessment repository",
            )
        cloned = False
    if not cloned and assessment_service._is_demo_workspace_fallback_enabled(claim):
        assessment_service._materialize_task_repository(sandbox, claim.task)
    elif not cloned:
        raise HTTPException(
            status_code=500,
            detail="Failed to initialize assessment repository",
        )

    repo_root = assessment_service._workspace_repo_root(claim.task)
    bootstrap = assessment_service._run_workspace_bootstrap(
        e2b,
        sandbox,
        claim.task,
        repo_root,
    )
    live_repo = None
    started_now = claim.status == AssessmentStatus.PENDING or not claim.started_at
    if not started_now:
        live_repo = assessment_service._read_sandbox_repo_files(sandbox, repo_root)
    _rollback(db)
    return bootstrap, live_repo


def _transition_application(db: Session, assessment: Assessment) -> None:
    if not assessment.application_id:
        return
    application = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == assessment.application_id,
            CandidateApplication.organization_id == assessment.organization_id,
        )
        .one_or_none()
    )
    if application is None:
        return
    ensure_pipeline_fields(application)
    initialize_pipeline_event_if_missing(
        db,
        app=application,
        actor_type="system",
        reason="Pipeline initialized at assessment start",
    )
    transition_stage(
        db,
        app=application,
        to_stage="in_assessment",
        source="system",
        actor_type="system",
        reason="Candidate started assessment",
        metadata={"assessment_id": assessment.id},
    )


def start_or_resume_assessment_impl(
    assessment: Assessment,
    db: Session,
) -> dict[str, Any]:
    """Run all slow start providers outside the request ORM transaction."""
    from ...components.assessments import service as assessment_service

    assessment_id = int(assessment.id)
    token = str(assessment.token or "")
    prepare_assessment_workspace_mutex(db)
    if not str(assessment_service.settings.E2B_API_KEY or "").strip():
        raise HTTPException(
            status_code=503,
            detail="Code environment is not configured. Please try again later.",
        )
    try:
        required_ai_mode = assessment_service.resolve_ai_mode()
    except Exception as exc:
        logger.exception("Assessment AI runtime is not configured")
        raise HTTPException(
            status_code=503,
            detail="The assessment isn't available right now. Please try again later.",
        ) from exc

    with assessment_workspace_mutex(db, assessment_id=assessment_id):
        claim = _capture_start_claim(db, assessment_id=assessment_id, token=token)
        e2b = assessment_service.E2BService(assessment_service.settings.E2B_API_KEY)
        sandbox = None
        created_new = False
        try:
            if claim.status == AssessmentStatus.IN_PROGRESS and claim.e2b_session_id:
                try:
                    sandbox = e2b.connect_sandbox(claim.e2b_session_id)
                except Exception:
                    sandbox = e2b.create_sandbox()
                    created_new = True
            else:
                sandbox = e2b.create_sandbox()
                created_new = True
            sandbox_id = str(e2b.get_sandbox_id(sandbox))

            try:
                claim = _ensure_branch(db, claim)
            except (HTTPException, WorkspaceClaimDriftError):
                raise
            except Exception:
                _rollback(db)
                logger.exception("Failed to create assessment repository branch")
                if not assessment_service._is_demo_workspace_fallback_enabled(claim):
                    raise HTTPException(
                        status_code=500,
                        detail="Failed to initialize assessment repository",
                    )
                logger.warning(
                    "Falling back to local task repo materialization for demo assessment=%s",
                    assessment_id,
                )

            bootstrap, live_repo = _initialize_workspace(
                db,
                claim,
                e2b,
                sandbox,
            )
            assessment_row, task = _revalidate_start_claim(db, claim)
            started_now = (
                claim.status == AssessmentStatus.PENDING or not claim.started_at
            )
            assessment_row.status = AssessmentStatus.IN_PROGRESS
            if started_now:
                assessment_row.started_at = utcnow()
                append_assessment_timeline_event(
                    assessment_row,
                    "assessment_started",
                    {"type": "started"},
                )
                seed_assessment_opener(assessment_row, task)
                _transition_application(db, assessment_row)
            if (
                claim.status == AssessmentStatus.PENDING
                and assessment_row.credit_consumed_at is None
            ):
                assessment_row.credit_consumed_at = utcnow()
            assessment_row.ai_mode = required_ai_mode
            assessment_row.e2b_session_id = sandbox_id
            if bootstrap.get("ran"):
                append_assessment_timeline_event(
                    assessment_row,
                    "workspace_bootstrap",
                    {
                        "success": bool(bootstrap.get("success")),
                        "must_succeed": bool(bootstrap.get("must_succeed")),
                        "working_dir": bootstrap.get("working_dir"),
                        "steps": bootstrap.get("steps") or [],
                    },
                )
            response = build_assessment_start_response(
                db,
                assessment_row,
                task,
                sandbox_id=sandbox_id,
                live_repo=live_repo,
                started_now=started_now,
            )
            db.commit()
            if not bootstrap.get("success") and bootstrap.get("must_succeed"):
                raise HTTPException(
                    status_code=500,
                    detail="Failed to prepare assessment workspace. Please try again later.",
                )
            return response
        except WorkspaceClaimDriftError as exc:
            _rollback(db)
            if created_new and sandbox is not None:
                _close_new_sandbox(db, e2b, sandbox, assessment_id)
            raise HTTPException(
                status_code=409,
                detail="Assessment changed while its workspace was starting. Please retry.",
            ) from exc
        except HTTPException:
            _rollback(db)
            if created_new and sandbox is not None:
                _close_new_sandbox(db, e2b, sandbox, assessment_id)
            raise
        except Exception as exc:
            _rollback(db)
            if created_new and sandbox is not None:
                _close_new_sandbox(db, e2b, sandbox, assessment_id)
            logger.exception(
                "Could not start code environment assessment_id=%s", assessment_id
            )
            raise HTTPException(
                status_code=503,
                detail="Could not start code environment. Please try again later.",
            ) from exc


__all__ = ["start_or_resume_assessment_impl"]
