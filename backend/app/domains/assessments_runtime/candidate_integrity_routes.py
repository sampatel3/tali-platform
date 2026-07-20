"""Candidate engagement and advisory browser-integrity events."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ...components.assessments.repository import (
    append_assessment_timeline_event,
    ensure_utc,
    get_active_assessment,
    utcnow,
    validate_assessment_token,
)
from ...components.assessments.service import enforce_active_or_timeout
from ...platform.database import get_db
from ...schemas.assessment import RuntimeEventRequest
from .candidate_auth import require_candidate_request_proof, validate_runtime_candidate_session
from .candidate_workspace import sanitize_repo_path

router = APIRouter()
logger = logging.getLogger(__name__)

# Engagement beacons dedupe. Advisory integrity events intentionally do not:
# repeated attempts are useful timeline evidence, though candidate-controlled
# signals must never be treated as proof on their own.
_DEDUPED_RUNTIME_EVENT_TYPES = frozenset({"runtime_loaded", "file_opened"})
_ADVISORY_INTEGRITY_EVENT_TYPES = frozenset(
    {
        "copy_attempt",
        "cut_attempt",
        "external_paste_blocked",
        "internal_paste",
        "print_attempt",
        "fullscreen_exit",
        "visibility_hidden",
        "drag_drop_blocked",
        "context_menu_blocked",
    }
)
_RUNTIME_EVENT_TYPES = _DEDUPED_RUNTIME_EVENT_TYPES | _ADVISORY_INTEGRITY_EVENT_TYPES


@router.post("/{assessment_id}/runtime-event")
def record_runtime_event(
    assessment_id: int,
    data: RuntimeEventRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    x_assessment_session: str | None = Header(None, description="Live candidate browser session key"),
    db: Session = Depends(get_db),
    _request_proof: None = Depends(require_candidate_request_proof),
):
    """Record bounded engagement or advisory integrity timeline evidence."""
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)
    validate_runtime_candidate_session(assessment, x_assessment_session)
    enforce_active_or_timeout(assessment, db)
    event_type = str(data.event_type or "").strip()
    if event_type not in _RUNTIME_EVENT_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported runtime event type")
    if event_type in _DEDUPED_RUNTIME_EVENT_TYPES:
        already = any(
            isinstance(event, dict) and event.get("event_type") == event_type
            for event in (assessment.timeline or [])
        )
        if already:
            return {"recorded": False, "reason": "already_recorded"}
    payload = {}
    if event_type == "runtime_loaded" and assessment.started_at is not None:
        payload["seconds_since_start"] = int(
            (utcnow() - ensure_utc(assessment.started_at)).total_seconds()
        )
    if event_type in _ADVISORY_INTEGRITY_EVENT_TYPES:
        if data.source is not None:
            payload["source"] = data.source
        if data.length is not None:
            payload["length"] = data.length
        safe_file_path = sanitize_repo_path(data.file_path)
        if safe_file_path:
            payload["file_path"] = safe_file_path
        payload["advisory"] = True
    append_assessment_timeline_event(assessment, event_type, payload)
    try:
        db.commit()
    except Exception:
        logger.exception("Failed to commit runtime event assessment_id=%s", assessment.id)
        db.rollback()
        return {"recorded": False, "reason": "commit_failed"}
    return {"recorded": True}
