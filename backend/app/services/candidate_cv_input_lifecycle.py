"""Transactional invalidation for candidate CV inputs shared by applications."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication


@dataclass(frozen=True)
class CandidateCvInputSnapshot:
    """Effective per-application CV generations before one candidate mutation."""

    organization_id: int
    candidate_id: int
    effective_fingerprints: dict[int, str | None]
    scoring_text_fingerprints: dict[int, str | None]


@dataclass(frozen=True)
class CandidateCvInvalidationResult:
    changed_application_ids: tuple[int, ...]
    owner_score_application_ids: tuple[int, ...]
    related_evaluation_ids: tuple[int, ...]


def _fingerprint(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _effective_cv_fingerprint(
    application: CandidateApplication,
    *,
    candidate_cv_text: object,
    candidate_cv_uploaded_at: object,
) -> str | None:
    application_text = str(getattr(application, "cv_text", None) or "").strip()
    if application_text:
        text = application_text
        uploaded_at = getattr(application, "cv_uploaded_at", None)
    else:
        text = str(candidate_cv_text or "").strip()
        uploaded_at = candidate_cv_uploaded_at
    if not text and uploaded_at is None:
        return None
    timestamp = (
        uploaded_at.isoformat()
        if hasattr(uploaded_at, "isoformat")
        else str(uploaded_at or "")
    )
    return _fingerprint(f"{text}\n{timestamp}")


def _effective_cv_text(
    application: CandidateApplication,
    *,
    candidate_cv_text: object,
) -> str:
    """Return the exact CV string consumed by related-role scoring."""

    return (
        str(getattr(application, "cv_text", None) or "").strip()
        or str(candidate_cv_text or "").strip()
    )


def capture_candidate_cv_input_snapshot(
    db: Session,
    *,
    candidate: Candidate,
    organization_id: int,
) -> CandidateCvInputSnapshot | None:
    """Capture the effective CV generation for every live application.

    Callers take this snapshot before changing either the shared Candidate CV
    or any application-owned copy. New applications are intentionally absent:
    their ordinary creation outbox owns first-time scoring.
    """

    candidate_id = getattr(candidate, "id", None)
    candidate_organization_id = getattr(candidate, "organization_id", None)
    if candidate_id is None:
        return None
    if (
        candidate_organization_id is None
        or int(candidate_organization_id) != int(organization_id)
    ):
        raise ValueError("Candidate does not belong to the requested organization")

    applications = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == int(organization_id),
            CandidateApplication.candidate_id == int(candidate_id),
            CandidateApplication.deleted_at.is_(None),
        )
        .order_by(CandidateApplication.id.asc())
        .all()
    )
    candidate_cv_text = getattr(candidate, "cv_text", None)
    candidate_cv_uploaded_at = getattr(candidate, "cv_uploaded_at", None)
    return CandidateCvInputSnapshot(
        organization_id=int(organization_id),
        candidate_id=int(candidate_id),
        effective_fingerprints={
            int(application.id): _effective_cv_fingerprint(
                application,
                candidate_cv_text=candidate_cv_text,
                candidate_cv_uploaded_at=candidate_cv_uploaded_at,
            )
            for application in applications
        },
        scoring_text_fingerprints={
            int(application.id): _fingerprint(
                _effective_cv_text(
                    application,
                    candidate_cv_text=candidate_cv_text,
                )
            )
            for application in applications
        },
    )


def invalidate_changed_candidate_cv_inputs(
    db: Session,
    *,
    candidate: Candidate,
    before: CandidateCvInputSnapshot | None,
    reason: str,
    queue_related_application_ids: set[int] | None = None,
) -> CandidateCvInvalidationResult:
    """Invalidate each owner/related score whose effective CV actually changed.

    This function only mutates durable database state; it never publishes paid
    work. ``queue_related_application_ids`` is application-scoped authority,
    not transport: passive provider refreshes and changed siblings remain
    ``stale_held`` while an explicit application score action may make only its
    named related evaluations ``pending`` for the existing durable sweep. Owner
    applications always receive the ordinary stale marker; their existing
    explicit score flow, if any, remains the only path that publishes a score.
    """

    if before is None:
        return CandidateCvInvalidationResult((), (), ())
    candidate_id = getattr(candidate, "id", None)
    organization_id = getattr(candidate, "organization_id", None)
    if (
        candidate_id is None
        or organization_id is None
        or int(candidate_id) != int(before.candidate_id)
        or int(organization_id) != int(before.organization_id)
    ):
        raise ValueError("Candidate CV snapshot identity changed")
    if not before.effective_fingerprints:
        return CandidateCvInvalidationResult((), (), ())

    application_ids = sorted(before.effective_fingerprints)
    applications = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id.in_(application_ids),
            CandidateApplication.organization_id == int(before.organization_id),
            CandidateApplication.candidate_id == int(before.candidate_id),
            CandidateApplication.deleted_at.is_(None),
        )
        .order_by(CandidateApplication.id.asc())
        .all()
    )
    candidate_cv_text = getattr(candidate, "cv_text", None)
    candidate_cv_uploaded_at = getattr(candidate, "cv_uploaded_at", None)
    changed = [
        application
        for application in applications
        if _effective_cv_fingerprint(
            application,
            candidate_cv_text=candidate_cv_text,
            candidate_cv_uploaded_at=candidate_cv_uploaded_at,
        )
        != before.effective_fingerprints.get(int(application.id))
    ]
    if not changed:
        return CandidateCvInvalidationResult((), (), ())

    # Imports stay local so low-level document/provider modules do not acquire
    # the scoring/task dependency graph at import time.
    from .cv_score_orchestrator import mark_application_scores_stale
    from .sister_role_evaluation_lifecycle import (
        reset_related_evaluations_for_application,
    )

    owner_score_application_ids: list[int] = []
    related_evaluation_ids: list[int] = []
    authorised_application_ids = {
        int(application_id)
        for application_id in (queue_related_application_ids or set())
    }
    for application in changed:
        scoring_text_changed = (
            _fingerprint(
                _effective_cv_text(
                    application,
                    candidate_cv_text=candidate_cv_text,
                )
            )
            != before.scoring_text_fingerprints.get(int(application.id))
        )
        if scoring_text_changed:
            related_evaluation_ids.extend(
                reset_related_evaluations_for_application(
                    db,
                    application,
                    reason=reason,
                    queue_for_rescore=(
                        int(application.id) in authorised_application_ids
                    ),
                )
            )
        if mark_application_scores_stale(
            db,
            int(application.id),
            reason=reason,
        ):
            owner_score_application_ids.append(int(application.id))

    return CandidateCvInvalidationResult(
        changed_application_ids=tuple(int(item.id) for item in changed),
        owner_score_application_ids=tuple(owner_score_application_ids),
        related_evaluation_ids=tuple(related_evaluation_ids),
    )


def replace_candidate_cv_and_invalidate(
    db: Session,
    *,
    candidate_id: int,
    organization_id: int,
    upload_result: dict[str, object],
    uploaded_at: object,
    reason: str,
    queue_related_application_ids: set[int] | None = None,
) -> Candidate | None:
    """Tenant-scoped Candidate CV mutation through the shared boundary."""

    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == int(candidate_id),
            Candidate.organization_id == int(organization_id),
        )
        .one_or_none()
    )
    if candidate is None:
        return None
    before = capture_candidate_cv_input_snapshot(
        db,
        candidate=candidate,
        organization_id=int(organization_id),
    )
    candidate.cv_file_url = upload_result.get("file_url")
    candidate.cv_filename = upload_result.get("filename")
    candidate.cv_text = upload_result.get("extracted_text")
    candidate.cv_uploaded_at = uploaded_at
    invalidate_changed_candidate_cv_inputs(
        db,
        candidate=candidate,
        before=before,
        reason=reason,
        queue_related_application_ids=queue_related_application_ids,
    )
    return candidate


__all__ = [
    "CandidateCvInputSnapshot",
    "CandidateCvInvalidationResult",
    "capture_candidate_cv_input_snapshot",
    "invalidate_changed_candidate_cv_inputs",
    "replace_candidate_cv_and_invalidate",
]
