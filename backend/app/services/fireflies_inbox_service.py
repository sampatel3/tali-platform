"""Durable Fireflies webhook ingestion and off-request transcript processing."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models.application_interview import ApplicationInterview
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.fireflies_webhook_inbox import (
    FIREFLIES_INBOX_FAILED,
    FIREFLIES_INBOX_IGNORED,
    FIREFLIES_INBOX_LINKED,
    FIREFLIES_INBOX_PENDING,
    FIREFLIES_INBOX_PROCESSING,
    FIREFLIES_INBOX_REVIEW_REQUIRED,
    FirefliesWebhookInbox,
)
from ..models.organization import Organization
from ..platform.secrets import decrypt_integration_secret
from .application_notes import create_interview_transcript_note
from .document_service import sanitize_json_for_storage, sanitize_text_for_storage
from .fireflies_service import (
    FirefliesService,
    attach_fireflies_match_metadata,
    normalize_email,
    normalized_transcript_bundle,
)
from .interview_support_service import refresh_application_interview_support
from .provider_error_evidence import safe_provider_error_code
from .scorecard_draft_service import maybe_autodraft_from_webhook


_MAX_ATTEMPTS = 8
_LEASE_SECONDS = 300


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _nested_get(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def enqueue_event(
    db: Session,
    *,
    organization_id: int,
    meeting_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> tuple[FirefliesWebhookInbox, bool]:
    """Persist one signed provider event, deduplicated at the database layer."""
    existing = (
        db.query(FirefliesWebhookInbox)
        .filter(
            FirefliesWebhookInbox.organization_id == int(organization_id),
            FirefliesWebhookInbox.meeting_id == meeting_id,
            FirefliesWebhookInbox.event_type == event_type,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing, False

    row = FirefliesWebhookInbox(
        organization_id=int(organization_id),
        meeting_id=meeting_id,
        event_type=event_type,
        payload=sanitize_json_for_storage(payload),
        status=FIREFLIES_INBOX_PENDING,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent provider retry may win the unique insert race.
        db.rollback()
        existing = (
            db.query(FirefliesWebhookInbox)
            .filter(
                FirefliesWebhookInbox.organization_id == int(organization_id),
                FirefliesWebhookInbox.meeting_id == meeting_id,
                FirefliesWebhookInbox.event_type == event_type,
            )
            .one()
        )
        return existing, False
    db.refresh(row)
    return row, True


def _candidate_emails(bundle: dict[str, Any]) -> list[str]:
    emails: list[str] = []
    organizer_email = normalize_email(bundle.get("organizer_email"))
    host_email = normalize_email(bundle.get("host_email"))
    invite_email = normalize_email(_nested_get(bundle, "taali_match", "fireflies_invite_email"))
    excluded = {item for item in {organizer_email, host_email, invite_email} if item}
    for raw in bundle.get("participants") or []:
        value = normalize_email(raw)
        if value and value not in excluded and value not in emails:
            emails.append(value)
    raw_payload = bundle.get("raw") if isinstance(bundle.get("raw"), dict) else {}
    attendees = raw_payload.get("meeting_attendees")
    for item in attendees if isinstance(attendees, list) else []:
        if isinstance(item, dict):
            value = normalize_email(item.get("email"))
            if value and value not in excluded and value not in emails:
                emails.append(value)
    return emails


def _applications(
    db: Session, *, organization_id: int, candidate_emails: list[str]
) -> list[CandidateApplication]:
    if not candidate_emails:
        return []
    return (
        db.query(CandidateApplication)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.organization_id == int(organization_id),
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
            Candidate.email.in_(candidate_emails),
        )
        .all()
    )


def meeting_linked_to_another_application(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
    provider_meeting_id: str | None,
) -> bool:
    """Check the provider-wide uniqueness contract before a manual link."""
    if not provider_meeting_id:
        return False
    existing = (
        db.query(ApplicationInterview.application_id)
        .filter(
            ApplicationInterview.organization_id == int(organization_id),
            ApplicationInterview.provider == "fireflies",
            ApplicationInterview.provider_meeting_id == provider_meeting_id,
        )
        .one_or_none()
    )
    return existing is not None and int(existing[0]) != int(application_id)


def _link_interview(
    db: Session,
    *,
    org: Organization,
    app: CandidateApplication,
    stage: str,
    bundle: dict[str, Any],
) -> ApplicationInterview:
    meeting_id = sanitize_text_for_storage(
        str(bundle.get("provider_meeting_id") or "").strip()
    ) or None
    interview = (
        db.query(ApplicationInterview)
        .filter(
            ApplicationInterview.organization_id == org.id,
            ApplicationInterview.provider == "fireflies",
            ApplicationInterview.provider_meeting_id == meeting_id,
        )
        .one_or_none()
    )
    if interview is None:
        interview = ApplicationInterview(
            organization_id=org.id,
            application_id=app.id,
            stage=stage,
            source="fireflies",
            provider="fireflies",
            provider_meeting_id=meeting_id,
        )
        db.add(interview)
        db.flush()
    elif interview.application_id != app.id:
        # Never silently re-parent an already-linked meeting.
        raise ValueError("Fireflies meeting is already linked to another application")
    interview.stage = stage
    interview.source = "fireflies"
    interview.provider = "fireflies"
    interview.provider_url = bundle.get("provider_url")
    interview.status = "completed"
    interview.transcript_text = bundle.get("transcript_text")
    interview.summary = bundle.get("summary")
    interview.speakers = bundle.get("speakers") if isinstance(bundle.get("speakers"), list) else []
    interview.provider_payload = attach_fireflies_match_metadata(
        bundle.get("raw") if isinstance(bundle.get("raw"), dict) else {},
        invite_email=getattr(org, "fireflies_invite_email", None),
        linked_via="webhook_auto_match",
        matched_application_id=app.id,
    )
    interview.meeting_date = bundle.get("meeting_date")
    interview.linked_at = _now()
    refresh_application_interview_support(app, organization=org)
    return interview


def _retry_delay(attempts: int, inbox_id: int) -> int:
    base = min(3600, 30 * (2 ** max(0, attempts - 1)))
    return base + ((int(inbox_id) * 37 + attempts * 17) % 16)


def _claim(db: Session, inbox_id: int) -> FirefliesWebhookInbox | None:
    now = _now()
    row = (
        db.query(FirefliesWebhookInbox)
        .filter(
            FirefliesWebhookInbox.id == int(inbox_id),
            or_(
                and_(
                    FirefliesWebhookInbox.status == FIREFLIES_INBOX_PENDING,
                    or_(
                        FirefliesWebhookInbox.next_attempt_at.is_(None),
                        FirefliesWebhookInbox.next_attempt_at <= now,
                    ),
                ),
                and_(
                    FirefliesWebhookInbox.status == FIREFLIES_INBOX_PROCESSING,
                    FirefliesWebhookInbox.lease_until <= now,
                ),
            ),
        )
        .with_for_update(skip_locked=True)
        .one_or_none()
    )
    if row is None:
        return None
    row.status = FIREFLIES_INBOX_PROCESSING
    row.attempts = int(row.attempts or 0) + 1
    row.lease_until = now + timedelta(seconds=_LEASE_SECONDS)
    row.next_attempt_at = None
    row.last_error = None
    db.commit()
    db.refresh(row)
    return row


def _finish(
    db: Session,
    row: FirefliesWebhookInbox,
    *,
    status: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    row.status = status
    row.result = sanitize_json_for_storage(result)
    row.lease_until = None
    row.next_attempt_at = None
    row.processed_at = _now()
    row.last_error = None
    db.commit()
    return result


def process_one(
    db: Session, *, inbox_id: int, max_attempts: int = _MAX_ATTEMPTS
) -> dict[str, Any]:
    """Claim and process one event. Safe for duplicate tasks and worker death."""
    row = _claim(db, inbox_id)
    if row is None:
        current = db.get(FirefliesWebhookInbox, int(inbox_id))
        if current is None:
            return {"status": "missing", "inbox_id": int(inbox_id)}
        return current.result or {"status": current.status, "inbox_id": current.id}

    try:
        org = db.get(Organization, row.organization_id)
        if org is None:
            return _finish(db, row, status=FIREFLIES_INBOX_FAILED, result={
                "status": "failed", "reason": "organization_not_found", "inbox_id": row.id
            })
        api_key = decrypt_integration_secret(getattr(org, "fireflies_api_key_encrypted", None))
        if not api_key:
            raise RuntimeError("Fireflies API key is not configured")
        meeting_id = str(row.meeting_id)
        # The durable claim is already committed. Release the read transaction
        # opened by loading its org/credential before the remote transcript GET.
        db.rollback()
        transcript = FirefliesService(api_key=api_key).get_transcript(meeting_id)
        if not transcript:
            raise RuntimeError("Fireflies transcript is not available yet")

        bundle = normalized_transcript_bundle(transcript)
        organizer = str(bundle.get("organizer_email") or "").strip().lower()
        configured_owner = str(getattr(org, "fireflies_owner_email", None) or "").strip().lower()
        if configured_owner and organizer and configured_owner != organizer:
            return _finish(db, row, status=FIREFLIES_INBOX_IGNORED, result={
                "status": "ignored", "reason": "owner_mismatch", "meeting_id": row.meeting_id
            })

        bundle["taali_match"] = {
            "fireflies_invite_email": normalize_email(getattr(org, "fireflies_invite_email", None))
        }
        candidate_emails = _candidate_emails(bundle)
        matches = _applications(
            db, organization_id=org.id, candidate_emails=candidate_emails
        )
        if len(matches) != 1:
            return _finish(db, row, status=FIREFLIES_INBOX_REVIEW_REQUIRED, result={
                "status": "review_required",
                "reason": "ambiguous_match" if matches else "no_match",
                "meeting_id": row.meeting_id,
                "candidate_emails": candidate_emails,
                "candidate_application_ids": [app.id for app in matches],
            })

        app = matches[0]
        stage = "tech_stage_2" if app.pipeline_stage == "review" else "screening"
        interview = _link_interview(db, org=org, app=app, stage=stage, bundle=bundle)
        create_interview_transcript_note(db, app=app, interview=interview, source_label="Fireflies")
        maybe_autodraft_from_webhook(db, org=org, app=app, interview=interview)
        return _finish(db, row, status=FIREFLIES_INBOX_LINKED, result={
            "status": "linked",
            "meeting_id": row.meeting_id,
            "application_id": app.id,
            "interview_id": interview.id,
        })
    except Exception as exc:
        db.rollback()
        row = db.get(FirefliesWebhookInbox, int(inbox_id))
        if row is None:
            raise
        row.last_error = safe_provider_error_code(
            exc,
            operation="fireflies_inbox",
        )
        row.lease_until = None
        if int(row.attempts or 0) >= int(max_attempts):
            row.status = FIREFLIES_INBOX_FAILED
            row.processed_at = _now()
            row.result = {"status": "failed", "reason": "retry_exhausted", "inbox_id": row.id}
            row.next_attempt_at = None
        else:
            row.status = FIREFLIES_INBOX_PENDING
            row.next_attempt_at = _now() + timedelta(
                seconds=_retry_delay(int(row.attempts or 0), int(row.id))
            )
        db.commit()
        return row.result or {
            "status": row.status,
            "inbox_id": row.id,
            "retry_at": row.next_attempt_at.isoformat() if row.next_attempt_at else None,
        }


def due_ids(db: Session, *, limit: int = 100) -> list[int]:
    """Return due/stale IDs for the recovery sweep; workers claim atomically."""
    now = _now()
    return [
        int(row_id)
        for (row_id,) in (
            db.query(FirefliesWebhookInbox.id)
            .filter(
                or_(
                    and_(
                        FirefliesWebhookInbox.status == FIREFLIES_INBOX_PENDING,
                        or_(
                            FirefliesWebhookInbox.next_attempt_at.is_(None),
                            FirefliesWebhookInbox.next_attempt_at <= now,
                        ),
                    ),
                    and_(
                        FirefliesWebhookInbox.status == FIREFLIES_INBOX_PROCESSING,
                        FirefliesWebhookInbox.lease_until <= now,
                    ),
                )
            )
            .order_by(FirefliesWebhookInbox.id.asc())
            .limit(max(1, min(int(limit), 1000)))
            .all()
        )
    ]


__all__ = [
    "enqueue_event",
    "process_one",
    "due_ids",
    "meeting_linked_to_another_application",
]
