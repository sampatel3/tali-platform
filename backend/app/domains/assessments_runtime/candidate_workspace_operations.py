"""Detached E2B operations for candidate execute and repository-save routes."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...components.assessments.repository import (
    append_assessment_timeline_event,
    get_active_assessment,
    validate_assessment_token,
)
from ...components.assessments.assessment_guards import enforce_not_paused
from ...components.assessments.service import enforce_active_or_timeout
from ...models.assessment import Assessment
from ...models.task import Task
from .workspace_claims import (
    AssessmentWorkspaceClaim,
    WorkspaceClaimDriftError,
    assessment_workspace_claim,
    lock_and_revalidate_workspace_claim,
)
from .workspace_serialization import (
    assessment_workspace_mutex,
    prepare_assessment_workspace_mutex,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkspaceOperationHooks:
    adapter_factory: Callable[[], Any]
    create_sandbox: Callable[..., Any]
    prepare_workspace: Callable[..., tuple[str, dict[str, Any]]]
    sync_files: Callable[[Any, str, dict[str, str]], None]
    run_selected_file: Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class _OpenWorkspace:
    e2b: Any
    sandbox: Any
    session_id: str
    repo_root: str
    bootstrap: dict[str, Any]
    created_new: bool


def _rollback(db: Session) -> None:
    if db.in_transaction():
        db.rollback()


def _capture_claim(
    db: Session,
    *,
    assessment_id: int,
    token: str,
    workspace_lock_held: bool = False,
) -> AssessmentWorkspaceClaim:
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, token)
    enforce_active_or_timeout(
        assessment,
        db,
        workspace_lock_held=workspace_lock_held,
    )
    enforce_not_paused(assessment)
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .populate_existing()
        .with_for_update(of=Assessment)
        .one_or_none()
    )
    if assessment is None:
        raise HTTPException(status_code=404, detail="Active assessment not found")
    validate_assessment_token(assessment, token)
    enforce_active_or_timeout(
        assessment,
        db,
        workspace_lock_held=workspace_lock_held,
    )
    enforce_not_paused(assessment)
    task = db.query(Task).filter(Task.id == assessment.task_id).one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    claim = assessment_workspace_claim(assessment, task)
    _rollback(db)
    return claim


def _open_workspace(
    db: Session,
    claim: AssessmentWorkspaceClaim,
    hooks: WorkspaceOperationHooks,
) -> _OpenWorkspace:
    _rollback(db)
    e2b = hooks.adapter_factory()
    created_new = False
    if claim.e2b_session_id:
        try:
            sandbox = e2b.connect_sandbox(claim.e2b_session_id)
        except Exception:
            sandbox = hooks.create_sandbox(e2b, claim)
            created_new = True
    else:
        sandbox = hooks.create_sandbox(e2b, claim)
        created_new = True
    try:
        session_id = str(e2b.get_sandbox_id(sandbox))
        repo_root, bootstrap = hooks.prepare_workspace(
            e2b,
            sandbox,
            claim,
            claim.task,
        )
        if not bootstrap.get("success") and bootstrap.get("must_succeed"):
            raise HTTPException(
                status_code=500,
                detail="Failed to prepare assessment workspace. Please try again later.",
            )
    except Exception:
        if created_new:
            try:
                e2b.close_sandbox(sandbox)
            except Exception:
                logger.exception(
                    "Failed to close rejected workspace assessment_id=%s",
                    claim.id,
                )
        raise
    return _OpenWorkspace(
        e2b=e2b,
        sandbox=sandbox,
        session_id=session_id,
        repo_root=repo_root,
        bootstrap=bootstrap,
        created_new=created_new,
    )


def _close_provisional_workspace(
    db: Session,
    opened: _OpenWorkspace | None,
    assessment_id: int,
) -> None:
    if opened is None or not opened.created_new:
        return
    _rollback(db)
    try:
        opened.e2b.close_sandbox(opened.sandbox)
    except Exception:
        logger.exception(
            "Failed to close provisional workspace assessment_id=%s",
            assessment_id,
        )


def _persist_operation(
    db: Session,
    claim: AssessmentWorkspaceClaim,
    opened: _OpenWorkspace,
    *,
    event_type: str,
    event_payload: dict[str, Any],
) -> None:
    assessment, _task = lock_and_revalidate_workspace_claim(db, claim)
    enforce_not_paused(assessment)
    assessment.e2b_session_id = opened.session_id
    if opened.bootstrap.get("ran"):
        append_assessment_timeline_event(
            assessment,
            "workspace_bootstrap",
            {
                "success": bool(opened.bootstrap.get("success")),
                "must_succeed": bool(opened.bootstrap.get("must_succeed")),
                "working_dir": opened.bootstrap.get("working_dir"),
                "steps": opened.bootstrap.get("steps") or [],
            },
        )
    append_assessment_timeline_event(assessment, event_type, event_payload)
    db.commit()


def execute_workspace_code(
    db: Session,
    *,
    assessment_id: int,
    token: str,
    code: str,
    selected_file_path: str,
    repo_files: dict[str, str],
    hooks: WorkspaceOperationHooks,
) -> dict[str, Any]:
    """Execute against a detached workspace, then CAS-persist session telemetry."""
    _capture_claim(db, assessment_id=assessment_id, token=token)
    prepare_assessment_workspace_mutex(db)
    with assessment_workspace_mutex(db, assessment_id=assessment_id):
        claim = _capture_claim(
            db,
            assessment_id=assessment_id,
            token=token,
            workspace_lock_held=True,
        )
        opened: _OpenWorkspace | None = None
        try:
            opened = _open_workspace(db, claim, hooks)
            if repo_files:
                hooks.sync_files(opened.sandbox, opened.repo_root, repo_files)
            started_at = time.time()
            if selected_file_path:
                result = hooks.run_selected_file(
                    opened.e2b,
                    opened.sandbox,
                    claim.task,
                    selected_file_path,
                )
            else:
                result = opened.e2b.execute_code(opened.sandbox, code)
                if isinstance(result, dict):
                    result.setdefault("command", None)
                    result.setdefault("working_dir", opened.repo_root)
            latency_ms = int((time.time() - started_at) * 1000)
            _persist_operation(
                db,
                claim,
                opened,
                event_type="code_execute",
                event_payload={
                    "session_id": opened.session_id,
                    "code_length": len(code),
                    "latency_ms": latency_ms,
                    "has_stderr": bool(result.get("stderr")),
                    "tests_passed": result.get("tests_passed"),
                    "tests_total": result.get("tests_total"),
                    "selected_file_path": selected_file_path,
                    "command": result.get("command"),
                },
            )
            return result
        except WorkspaceClaimDriftError as exc:
            _rollback(db)
            _close_provisional_workspace(db, opened, assessment_id)
            raise HTTPException(
                status_code=409,
                detail="Assessment changed while code was running. Please retry.",
            ) from exc
        except HTTPException:
            _rollback(db)
            _close_provisional_workspace(db, opened, assessment_id)
            raise
        except Exception as exc:
            _rollback(db)
            _close_provisional_workspace(db, opened, assessment_id)
            logger.exception(
                "Failed to persist candidate code execution assessment_id=%s",
                assessment_id,
            )
            raise HTTPException(
                status_code=500,
                detail="Failed to persist assessment workspace state",
            ) from exc


def save_workspace_files(
    db: Session,
    *,
    assessment_id: int,
    token: str,
    files: dict[str, str],
    hooks: WorkspaceOperationHooks,
) -> dict[str, Any]:
    """Save repository files detached from ORM state, then CAS-persist telemetry."""
    _capture_claim(db, assessment_id=assessment_id, token=token)
    prepare_assessment_workspace_mutex(db)
    with assessment_workspace_mutex(db, assessment_id=assessment_id):
        claim = _capture_claim(
            db,
            assessment_id=assessment_id,
            token=token,
            workspace_lock_held=True,
        )
        opened: _OpenWorkspace | None = None
        try:
            opened = _open_workspace(db, claim, hooks)
            hooks.sync_files(opened.sandbox, opened.repo_root, files)
            primary_path = next(iter(files), "")
            _persist_operation(
                db,
                claim,
                opened,
                event_type="repo_file_save",
                event_payload={
                    "session_id": opened.session_id,
                    "path": primary_path,
                    "paths": list(files),
                    "file_count": len(files),
                    "content_length": sum(len(value) for value in files.values()),
                },
            )
            return {
                "success": True,
                "path": primary_path,
                "paths": list(files),
                "file_count": len(files),
                "message": (
                    f"Saved {len(files)} file(s)"
                    if len(files) != 1
                    else f"Saved {primary_path}"
                ),
            }
        except WorkspaceClaimDriftError as exc:
            _rollback(db)
            _close_provisional_workspace(db, opened, assessment_id)
            raise HTTPException(
                status_code=409,
                detail="Assessment changed while files were saving. Please retry.",
            ) from exc
        except HTTPException:
            _rollback(db)
            _close_provisional_workspace(db, opened, assessment_id)
            raise
        except Exception as exc:
            _rollback(db)
            _close_provisional_workspace(db, opened, assessment_id)
            logger.exception(
                "Failed to persist candidate file save assessment_id=%s",
                assessment_id,
            )
            raise HTTPException(
                status_code=500,
                detail="Failed to persist assessment workspace state",
            ) from exc


__all__ = [
    "WorkspaceOperationHooks",
    "execute_workspace_code",
    "save_workspace_files",
]
