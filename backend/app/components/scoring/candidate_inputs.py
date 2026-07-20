"""Deterministic generation checks for mutable candidate scoring inputs."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...services.workable_context_service import format_workable_context


_CANDIDATE_CONTEXT_FIELDS = (
    "full_name", "headline", "location_city", "location_country", "phone",
    "email", "profile_url", "social_profiles", "summary", "workable_data",
    "skills", "tags", "education_entries", "experience_entries",
    "workable_comments", "workable_activities",
)
_APPLICATION_INPUT_FIELDS = (
    "cv_text", "cv_uploaded_at", "workable_stage", "workable_sourced",
    "workable_answers", "workable_comments", "workable_activities",
)


def candidate_input_fingerprint(application: Any, candidate: Any) -> str:
    """Hash exactly the candidate data rendered into scoring prompts."""
    cv_uploaded_at = getattr(application, "cv_uploaded_at", None)
    payload = "\n".join(
        (
            str(getattr(application, "cv_text", "") or "").strip(),
            cv_uploaded_at.isoformat() if cv_uploaded_at is not None else "",
            format_workable_context(candidate, application),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def candidate_input_fingerprint_from_db(
    db: Session,
    *,
    application_id: int,
    candidate_id: int,
    organization_id: int,
    role_id: int,
    lock: bool = False,
) -> str | None:
    """Project and optionally lock the committed candidate scoring inputs.

    Candidate precedes CandidateApplication in the lock order. Projection keeps
    large ORM relationships out of the worker's generation comparison.
    """
    candidate_query = db.query(
        *(getattr(Candidate, name) for name in _CANDIDATE_CONTEXT_FIELDS)
    ).filter(
        Candidate.id == int(candidate_id),
        Candidate.organization_id == int(organization_id),
        Candidate.deleted_at.is_(None),
    )
    if lock:
        candidate_query = candidate_query.with_for_update(of=Candidate)
    candidate_row = candidate_query.one_or_none()
    if candidate_row is None:
        return None

    application_query = db.query(
        *(getattr(CandidateApplication, name) for name in _APPLICATION_INPUT_FIELDS)
    ).filter(
        CandidateApplication.id == int(application_id),
        CandidateApplication.candidate_id == int(candidate_id),
        CandidateApplication.organization_id == int(organization_id),
        CandidateApplication.role_id == int(role_id),
        CandidateApplication.deleted_at.is_(None),
    )
    if lock:
        application_query = application_query.with_for_update(
            of=CandidateApplication
        )
    application_row = application_query.one_or_none()
    if application_row is None:
        return None

    candidate = SimpleNamespace(
        **dict(zip(_CANDIDATE_CONTEXT_FIELDS, candidate_row, strict=True))
    )
    application = SimpleNamespace(
        **dict(zip(_APPLICATION_INPUT_FIELDS, application_row, strict=True))
    )
    return candidate_input_fingerprint(application, candidate)


__all__ = [
    "candidate_input_fingerprint",
    "candidate_input_fingerprint_from_db",
]
