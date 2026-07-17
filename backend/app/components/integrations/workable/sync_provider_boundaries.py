"""Transaction-boundary primitives for the Workable pull-sync worker.

Provider reads and object-store uploads can be slow or ambiguous.  The sync
worker must never keep an ORM transaction (and therefore a pooled connection or
row lock) open while one is in flight.  These immutable claims carry only the
minimum primitive identity needed to validate a result in a fresh transaction.
"""

from __future__ import annotations

import logging
import mimetypes
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from ....models.role import Role
from ....services.document_service import (
    extract_text,
    sanitize_json_for_storage,
    sanitize_text_for_storage,
)
from .service import WorkableRateLimitError, WorkableService

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ....models.candidate import Candidate
    from ....models.candidate_application import CandidateApplication


class WorkableSyncCancelled(Exception):
    """The user requested cancellation at a committed sync boundary."""


class WorkableProviderLineageDrift(RuntimeError):
    """Remote results belong to a superseded org/job credential generation."""


def finish_db_phase(db: Session) -> None:
    """Commit the current short DB phase and prove the connection is released.

    Callers intentionally commit rather than roll back: a boundary can follow a
    progress/cancellation checkpoint or another completed candidate, and none
    of that durable work may be discarded merely to make a provider call safe.
    """

    db.commit()
    assert_provider_ready(db)


def assert_provider_ready(db: Session) -> None:
    """Fail closed if provider I/O is attempted with an ORM transaction open."""

    if db.in_transaction():
        raise RuntimeError("Workable provider call attempted inside a database transaction")


def fetch_candidate_activities(
    client: WorkableService,
    candidate_id: str,
) -> tuple[list, list] | None:
    """Fetch and split a Workable activity feed without mutating ORM rows."""

    try:
        activities = client.get_candidate_activities(candidate_id)
        if activities is not None:
            comments = [item for item in activities if item.get("action") == "comment"]
            others = [item for item in activities if item.get("action") != "comment"]
            return comments, others
    except WorkableRateLimitError:
        raise
    except Exception:
        logger.debug("Workable activities fetch failed for candidate_id=%s", candidate_id)
    return None


def fetch_role_stages(
    client: WorkableService,
    claim: RoleProviderClaim,
    shortcode: str | None,
    *,
    ttl: timedelta,
) -> list[dict] | None:
    """TTL-gated stage read from a primitive role claim."""

    if not shortcode:
        return None
    synced_at = claim.stages_synced_at
    if claim.has_stages and synced_at is not None:
        if synced_at.tzinfo is None:
            synced_at = synced_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - synced_at < ttl:
            return None
    try:
        stages = client.list_job_stages(shortcode)
    except Exception as exc:
        logger.warning(
            "Failed to refresh Workable stages role_id=%s error_type=%s",
            claim.role_id,
            type(exc).__name__,
        )
        return None
    return sanitize_json_for_storage(stages) if stages else None


@dataclass(frozen=True, slots=True, repr=False)
class RoleProviderClaim:
    organization_id: int
    organization_auth_fingerprint: str
    role_id: int | None
    role_version: int | None
    workable_job_id: str
    has_stages: bool
    stages_synced_at: datetime | None


@dataclass(frozen=True, slots=True, repr=False)
class CandidateProviderClaim:
    organization_id: int
    organization_auth_fingerprint: str
    run_id: int | None
    run_status: str | None
    role_id: int
    role_version: int
    workable_job_id: str
    candidate_external_id: str
    application_id: int | None
    application_version: int | None
    candidate_id: int | None
    candidate_workable_id: str | None
    candidate_email: str | None
    candidate_phone_normalized: str | None
    candidate_identity_fingerprint: str
    application_state_fingerprint: str
    application_cv_fingerprint: str
    candidate_cv_fingerprint: str
    resolved: bool
    activities_due: bool
    needs_resume: bool


def build_role_provider_claim(
    db: Session,
    org: object,
    workable_job_id: str,
) -> RoleProviderClaim:
    """Snapshot role cache and organization credential lineage in one DB phase."""

    organization_id = int(getattr(org, "id"))
    row = None
    if workable_job_id:
        row = (
            db.query(
                Role.id,
                Role.version,
                Role.workable_stages,
                Role.workable_stages_synced_at,
            )
            .filter(
                Role.organization_id == organization_id,
                Role.workable_job_id == workable_job_id,
            )
            .first()
        )
    return RoleProviderClaim(
        organization_id=organization_id,
        organization_auth_fingerprint=workable_org_auth_fingerprint(org),
        role_id=int(row.id) if row is not None else None,
        role_version=int(row.version or 1) if row is not None else None,
        workable_job_id=workable_job_id,
        has_stages=bool(row.workable_stages) if row is not None else False,
        stages_synced_at=(row.workable_stages_synced_at if row is not None else None),
    )


def claim_role_provider_wave(
    db: Session,
    org: object,
    workable_job_id: str,
    role_id: int,
    *,
    expected_org_fingerprint: str | None = None,
) -> RoleProviderClaim:
    """Capture and release the exact role/auth generation for remote reads."""

    claim = build_role_provider_claim(db, org, workable_job_id)
    if (
        claim.role_id != role_id
        or (
            expected_org_fingerprint is not None
            and claim.organization_auth_fingerprint != expected_org_fingerprint
        )
    ):
        db.rollback()
        raise WorkableProviderLineageDrift(
            "Workable role lineage changed before provider read"
        )
    finish_db_phase(db)
    return claim


def candidate_claim_matches_role(
    candidate: CandidateProviderClaim,
    role: RoleProviderClaim,
) -> bool:
    return (
        candidate.organization_id == role.organization_id
        and candidate.organization_auth_fingerprint == role.organization_auth_fingerprint
        and candidate.role_id == role.role_id
        and candidate.role_version == role.role_version
        and candidate.workable_job_id == role.workable_job_id
    )


@dataclass(frozen=True, slots=True, repr=False)
class ResumeUpload:
    """Sanitized resume material ready for a detached object-store upload."""

    filename: str
    extracted_text: str
    extension: str
    content_type: str
    content: bytes = field(repr=False)


def workable_org_auth_fingerprint(org: object) -> str:
    """Hash provider lineage without retaining credentials in claims or logs."""

    parts = (
        "1" if bool(getattr(org, "workable_connected", False)) else "0",
        str(getattr(org, "workable_subdomain", None) or ""),
        str(getattr(org, "workable_access_token", None) or ""),
        str(getattr(org, "workable_refresh_token", None) or ""),
    )
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def prepare_resume_upload(filename: str, content: bytes) -> ResumeUpload | None:
    """Validate/extract a Workable resume without touching the database."""

    if not content:
        return None
    safe_filename = sanitize_text_for_storage(filename)
    extension = (safe_filename.rsplit(".", 1)[-1] if "." in safe_filename else "").lower()
    preview_only_extensions = {"pdf", "png", "jpg", "jpeg", "webp"}
    text_extensions = {"pdf", "docx", "txt"}
    if extension not in text_extensions | preview_only_extensions:
        return None
    extracted = (
        sanitize_text_for_storage(extract_text(content, extension))
        if extension in text_extensions
        else ""
    )
    if not extracted and extension not in preview_only_extensions:
        return None
    return ResumeUpload(
        filename=safe_filename,
        extracted_text=extracted,
        extension=extension,
        content_type=mimetypes.guess_type(safe_filename)[0] or "application/octet-stream",
        content=content,
    )


def apply_resume_upload(
    *,
    app: CandidateApplication,
    candidate: Candidate,
    upload: ResumeUpload,
    file_url: str,
    uploaded_at: datetime,
) -> None:
    """Apply a detached upload result to freshly revalidated ORM rows."""

    app.cv_file_url = file_url
    app.cv_filename = upload.filename
    app.cv_text = upload.extracted_text
    app.cv_uploaded_at = uploaded_at
    if upload.extension == "pdf":
        from ....services.document_hygiene import stash_pdf_hygiene_on_application

        stash_pdf_hygiene_on_application(app, upload.content, upload.extension)
    candidate.cv_file_url = file_url
    candidate.cv_filename = upload.filename
    candidate.cv_text = upload.extracted_text
    candidate.cv_uploaded_at = uploaded_at


__all__ = [
    "CandidateProviderClaim",
    "ResumeUpload",
    "RoleProviderClaim",
    "WorkableSyncCancelled",
    "WorkableProviderLineageDrift",
    "apply_resume_upload",
    "assert_provider_ready",
    "build_role_provider_claim",
    "candidate_claim_matches_role",
    "claim_role_provider_wave",
    "fetch_candidate_activities",
    "fetch_role_stages",
    "finish_db_phase",
    "prepare_resume_upload",
    "workable_org_auth_fingerprint",
]
