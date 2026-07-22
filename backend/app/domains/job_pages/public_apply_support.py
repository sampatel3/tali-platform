"""Validation and persistence helpers for the public application endpoint."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import HTTPException, UploadFile
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from ...models.candidate_application import CandidateApplication
from ...models.role import ROLE_KIND_SISTER, Role

logger = logging.getLogger("taali.job_pages")

APPLY_EMAIL_REQUIRED_MESSAGE = (
    "Please provide a valid email address so we can send the assessment."
)
_APPLY_RESUME_TYPE_MESSAGE = "Please upload your resume as a PDF or Word document."
_APPLY_RESUME_UNREADABLE_MESSAGE = (
    "We couldn't read any text from that resume. Please upload a text-based PDF "
    "or Word document."
)
_EMAIL_ADAPTER = TypeAdapter(EmailStr)
_RESUME_ALLOWED_EXTENSIONS = {"pdf", "docx"}


def role_requires_email(role: Role | None) -> bool:
    """Assessment invites are delivered by email; phone-only cannot progress."""
    if role is None:
        return False
    from ...agent_runtime.decision_translation import role_has_assessment_stage

    return role_has_assessment_stage(role)


def usable_email(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return str(_EMAIL_ADAPTER.validate_python(raw)).strip().lower()
    except ValidationError:
        return None


def parse_answers(raw: str | None) -> dict:
    """Parse the multipart answers JSON object, with a candidate-safe error."""
    if raw is None or raw.strip() == "":
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Answers must be valid JSON.")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail="Answers must be a JSON object.")
    return parsed


def attach_resume(
    db: Session,
    application: CandidateApplication,
    org_id: int,
    upload: UploadFile,
) -> None:
    """Validate, extract and attach a resume through the shared CV path."""
    from ...services.document_hygiene import stash_pdf_hygiene_on_application
    from ...services.candidate_cv_input_lifecycle import (
        capture_candidate_cv_input_snapshot,
        invalidate_changed_candidate_cv_inputs,
    )
    from ...services.document_service import (
        load_stored_document_bytes,
        process_document_upload,
        sanitize_text_for_storage,
    )

    candidate = application.candidate
    cv_snapshot = (
        capture_candidate_cv_input_snapshot(
            db,
            candidate=candidate,
            organization_id=int(org_id),
        )
        if candidate is not None
        else None
    )
    filename = (upload.filename or "").strip()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _RESUME_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail=_APPLY_RESUME_TYPE_MESSAGE)

    result = process_document_upload(
        upload=upload,
        entity_id=int(application.id),
        doc_type="cv",
        allowed_extensions=_RESUME_ALLOWED_EXTENSIONS,
    )
    now = datetime.now(timezone.utc)
    text = sanitize_text_for_storage(result["extracted_text"])
    if not text.strip():
        # Without OCR this application could never enter autonomous scoring.
        raise HTTPException(status_code=422, detail=_APPLY_RESUME_UNREADABLE_MESSAGE)
    application.cv_file_url = result["file_url"]
    application.cv_filename = result["filename"]
    application.cv_text = text
    application.cv_uploaded_at = now
    if candidate:
        candidate.cv_file_url = result["file_url"]
        candidate.cv_filename = result["filename"]
        candidate.cv_text = text
        candidate.cv_uploaded_at = now
        role = db.get(Role, int(application.role_id))
        invalidate_changed_candidate_cv_inputs(
            db,
            candidate=candidate,
            before=cv_snapshot,
            reason="candidate_cv_replaced",
            queue_related_application_ids=(
                {int(application.id)}
                if role is not None
                and str(role.role_kind or "") == ROLE_KIND_SISTER
                else None
            ),
        )
    try:
        content = load_stored_document_bytes(result["file_url"])
        if content:
            stash_pdf_hygiene_on_application(application, content, ext)
    except Exception:  # pragma: no cover - hygiene evidence is best-effort
        logger.warning("resume hygiene scan skipped for application_id=%s", application.id)


def find_existing_application(
    db: Session,
    org_id: int,
    role: Role,
    email: str | None,
    phone: str | None,
) -> CandidateApplication | None:
    """Re-read the winning application after a concurrent identity insert."""
    from ...services.candidate_identity_service import resolve_candidate

    candidate = resolve_candidate(db, org_id, email=email, phone=phone)
    if candidate is None:
        return None
    return (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == org_id,
            CandidateApplication.candidate_id == candidate.id,
            CandidateApplication.role_id == role.id,
            CandidateApplication.deleted_at.is_(None),
        )
        .first()
    )
