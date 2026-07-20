"""Candidate editor, run, and sandbox-lifetime endpoints."""

from __future__ import annotations

import hashlib
import logging
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from ...components.assessments.repository import (
    append_assessment_timeline_event,
    get_active_assessment,
    time_remaining_seconds,
    validate_assessment_token,
)
from ...components.assessments.service import enforce_active_or_timeout, enforce_not_paused
from ...domains.integrations_notifications.adapters import build_sandbox_adapter
from ...platform.database import get_db
from ...schemas.assessment import CodeExecutionRequest, RepoFileSaveRequest
from . import candidate_runtime_routes as runtime_core
from .candidate_auth import (
    candidate_runtime_operation,
    require_candidate_request_proof,
    validate_runtime_candidate_session,
)
from .candidate_workspace import (
    MAX_CANDIDATE_FILE_BYTES,
    bounded_execution_result,
    candidate_file_revision,
    read_candidate_repo_file,
    run_selected_repo_file,
    sanitize_repo_path,
    sync_repo_files_to_sandbox,
)


router = APIRouter()
logger = logging.getLogger(__name__)
_execute_runtime_operation = candidate_runtime_operation("execute")
_read_runtime_operation = candidate_runtime_operation("read")
_save_runtime_operation = candidate_runtime_operation("save")


def _require_matching_file_revision(
    *,
    data: object,
    sandbox: object,
    repo_root: str,
    path: str,
) -> dict | None:
    """Enforce one clear optimistic-write contract for Save and Run."""
    if "base_revision" not in getattr(data, "model_fields_set", set()):
        raise HTTPException(
            status_code=428,
            detail={
                "code": "FILE_REVISION_REQUIRED",
                "message": "Reload this file before saving or running it.",
                "path": path,
            },
        )
    current = read_candidate_repo_file(
        sandbox,
        repo_root,
        path,
        allow_missing=True,
    )
    expected = getattr(data, "base_revision", None)
    current_revision = current.get("revision") if current else None
    matches = (
        (current is None and expected is None)
        or (current is not None and expected is not None and expected == current_revision)
    )
    if not matches:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "FILE_REVISION_CONFLICT",
                "message": "This file changed in the workspace. Review the latest version before overwriting it.",
                "path": path,
                "current_revision": current_revision,
            },
        )
    return current


@router.post("/{assessment_id}/execute")
def execute_code(
    assessment_id: int,
    data: CodeExecutionRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    x_assessment_session: str | None = Header(None, description="Live candidate browser session key"),
    db: Session = Depends(get_db),
    _operation_id: str = Depends(_execute_runtime_operation),
):
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)
    validate_runtime_candidate_session(assessment, x_assessment_session)
    enforce_active_or_timeout(assessment, db)
    enforce_not_paused(assessment)
    if data.repo_files:
        raise HTTPException(status_code=400, detail="Bulk repository replacement is disabled")
    task = runtime_core._load_assessment_task(assessment, db)

    selected_path = sanitize_repo_path(data.selected_file_path)
    if not selected_path:
        raise HTTPException(
            status_code=400,
            detail="Select a runnable repository file. Raw code execution is disabled.",
        )
    e2b = build_sandbox_adapter()
    sandbox, repo_root = runtime_core._connect_assessment_sandbox(e2b, assessment, task, db)
    _require_matching_file_revision(
        data=data,
        sandbox=sandbox,
        repo_root=repo_root,
        path=selected_path,
    )
    sync_repo_files_to_sandbox(sandbox, repo_root, {selected_path: data.code})
    saved_revision = candidate_file_revision(data.code)
    started_at = time.time()
    result = bounded_execution_result(
        run_selected_repo_file(e2b, sandbox, task, selected_path),
        repo_root=repo_root,
    )
    append_assessment_timeline_event(
        assessment,
        "code_execute",
        {
            "session_id": assessment.e2b_session_id,
            "code_length": len(data.code or ""),
            "latency_ms": int((time.time() - started_at) * 1000),
            "has_stderr": bool(result.get("stderr")),
            "tests_passed": result.get("tests_passed"),
            "tests_total": result.get("tests_total"),
            "selected_file_path": selected_path,
            "command": result.get("command"),
        },
    )
    result.update(path=selected_path, revision=saved_revision)
    try:
        db.commit()
    except Exception:
        logger.exception("Failed to commit code_execute timeline event assessment_id=%s", assessment.id)
        db.rollback()
    return result


@router.post("/{assessment_id}/keepalive")
def keepalive_assessment_workspace(
    assessment_id: int,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    x_assessment_session: str | None = Header(None, description="Live candidate browser session key"),
    db: Session = Depends(get_db),
    _request_proof: None = Depends(require_candidate_request_proof),
):
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)
    validate_runtime_candidate_session(assessment, x_assessment_session)
    enforce_active_or_timeout(assessment, db)
    enforce_not_paused(assessment)
    if not assessment.e2b_session_id:
        raise HTTPException(status_code=503, detail="The assessment workspace session is unavailable")
    e2b = build_sandbox_adapter()
    try:
        sandbox = e2b.connect_sandbox(assessment.e2b_session_id)
        e2b.touch_sandbox(sandbox)
    except Exception as exc:  # noqa: BLE001 - provider-specific connection errors
        logger.info(
            "Candidate workspace keepalive failed assessment_id=%s error=%s",
            assessment.id,
            exc.__class__.__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="The workspace could not be kept active. Your saved work has not been replaced.",
        ) from exc
    return {"success": True, "time_remaining": time_remaining_seconds(assessment)}


@router.get("/{assessment_id}/repo-file")
def get_repo_file(
    assessment_id: int,
    path: str = Query(..., min_length=1, max_length=500),
    x_assessment_token: str = Header(..., description="Assessment access token"),
    x_assessment_session: str | None = Header(None, description="Live candidate browser session key"),
    db: Session = Depends(get_db),
    _operation_id: str = Depends(_read_runtime_operation),
):
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)
    validate_runtime_candidate_session(assessment, x_assessment_session)
    enforce_active_or_timeout(assessment, db)
    enforce_not_paused(assessment)
    safe_path = sanitize_repo_path(path)
    if not safe_path:
        raise HTTPException(status_code=400, detail="Invalid or protected repository file path")
    task = runtime_core._load_assessment_task(assessment, db)
    e2b = build_sandbox_adapter()
    sandbox, repo_root = runtime_core._connect_assessment_sandbox(e2b, assessment, task, db)
    file_payload = read_candidate_repo_file(sandbox, repo_root, safe_path)
    if file_payload is None:  # pragma: no cover - allow_missing is false
        raise HTTPException(status_code=404, detail="Repository file not found")
    append_assessment_timeline_event(
        assessment,
        "file_opened",
        {
            "path": safe_path,
            "byte_length": int(file_payload["byte_length"]),
            "source": "repo_file_api",
        },
    )
    try:
        db.commit()
    except Exception as exc:  # pragma: no cover - DB failure perimeter
        db.rollback()
        logger.exception(
            "Failed to persist file_opened event assessment_id=%s path=%s",
            assessment.id,
            safe_path,
        )
        raise HTTPException(status_code=503, detail="Workspace file access is temporarily unavailable") from exc
    return {
        "path": safe_path,
        "content": file_payload["content"],
        "revision": file_payload["revision"],
    }


@router.post("/{assessment_id}/repo-file")
def save_repo_file(
    assessment_id: int,
    data: RepoFileSaveRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    x_assessment_session: str | None = Header(None, description="Live candidate browser session key"),
    db: Session = Depends(get_db),
    _operation_id: str = Depends(_save_runtime_operation),
):
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)
    validate_runtime_candidate_session(assessment, x_assessment_session)
    enforce_active_or_timeout(assessment, db)
    enforce_not_paused(assessment)
    task = runtime_core._load_assessment_task(assessment, db)
    if data.files:
        raise HTTPException(status_code=400, detail="Bulk repository replacement is disabled")
    safe_path = sanitize_repo_path(data.path)
    if not safe_path:
        raise HTTPException(status_code=400, detail="Invalid or protected repository file path")
    content = str(data.content or "")
    if len(content.encode("utf-8")) > MAX_CANDIDATE_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Repository file is too large")
    e2b = build_sandbox_adapter()
    sandbox, repo_root = runtime_core._connect_assessment_sandbox(e2b, assessment, task, db)
    _require_matching_file_revision(
        data=data,
        sandbox=sandbox,
        repo_root=repo_root,
        path=safe_path,
    )
    sync_repo_files_to_sandbox(sandbox, repo_root, {safe_path: content})
    saved_revision = candidate_file_revision(content)
    append_assessment_timeline_event(
        assessment,
        "repo_file_save",
        {
            "session_id": assessment.e2b_session_id,
            "path": safe_path,
            "paths": [safe_path],
            "file_count": 1,
            "content_length": len(content),
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "source": "repo_file_api",
        },
    )
    try:
        db.commit()
    except Exception:
        logger.exception("Failed to commit repo_file_save timeline event assessment_id=%s", assessment.id)
        db.rollback()
    return {
        "success": True,
        "path": safe_path,
        "revision": saved_revision,
        "paths": [safe_path],
        "file_count": 1,
        "message": f"Saved {safe_path}",
    }
